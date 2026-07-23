#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
MODEL_LABEL="${MODEL_LABEL:-qwen3.5-9b-100k}"
MYSQL_SOURCE_ID="${MYSQL_SOURCE_ID:-mysql}"
MONGODB_SOURCE_ID="${MONGODB_SOURCE_ID:-mongodb}"
PREFLIGHT_ONLY=false

if [[ "${1:-}" == "--preflight-only" ]]; then
  PREFLIGHT_ONLY=true
fi

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

read_env_value() {
  local key="$1"
  local file="${2:-.env}"
  [[ -f "$file" ]] || return 1
  sed -n "s/^${key}=//p" "$file" | tail -n 1 | tr -d '\r'
}

wait_for_api() {
  printf 'Waiting for QueryX API at %s...\n' "$BASE_URL"
  for _ in $(seq 1 60); do
    if curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
      printf 'QueryX API is ready.\n'
      return 0
    fi
    sleep 2
  done
  fail "QueryX API did not become ready in time"
}

ask_model_warmup() {
  local choice

  echo
  echo "Ollama model warm-up"
  echo "--------------------"
  echo "The model can be loaded into memory before continuing."
  echo "This avoids including the cold-start time in the first query or benchmark case."
  echo

  while true; do
    read -r -p "Warm up Ollama model '${OLLAMA_MODEL}' now? [Y/n]: " choice

    case "${choice:-Y}" in
      y|Y|yes|YES|Yes)
        return 0
        ;;
      n|N|no|NO|No)
        return 1
        ;;
      *)
        echo "Invalid choice. Enter y or n."
        ;;
    esac
  done
}

warmup_model() {
  local payload response started_at finished_at elapsed

  echo
  echo "Loading Ollama model into memory..."
  echo "Model: ${OLLAMA_MODEL}"

  payload="$(
    OLLAMA_MODEL="$OLLAMA_MODEL" python3 - <<'PY'
import json
import os

print(json.dumps({
    "model": os.environ["OLLAMA_MODEL"],
    "prompt": (
        "Respond only with the word READY. "
        "This is a warm-up request used before running QueryX."
    ),
    "stream": False,
    "keep_alive": "-1m",
    "options": {
        "temperature": 0
    }
}))
PY
  )"

  started_at="$(date +%s)"

  response="$(
    curl -fsS \
      -X POST http://localhost:11434/api/generate \
      -H 'Content-Type: application/json' \
      --data "$payload"
  )" || fail "Ollama model warm-up failed"

  finished_at="$(date +%s)"
  elapsed=$((finished_at - started_at))

  echo "Ollama model is warm and ready."
  printf 'Warm-up completed in %02d:%02d.\n' \
    $((elapsed / 60)) $((elapsed % 60))

  if command -v jq >/dev/null 2>&1; then
    printf 'Model response: %s\n' \
      "$(printf '%s\n' "$response" | jq -r '.response // "READY"')"
  fi
}

print_phase() {
  local current="$1"
  local total="$2"
  local message="$3"
  printf '\n[%s/%s] %s\n' "$current" "$total" "$message"
}

run_with_status() {
  local label="$1"
  shift

  local started_at
  started_at="$(date +%s)"

  "$@" &
  local command_pid=$!

  local frames=('|' '/' '-' '\')
  local frame=0

  while kill -0 "$command_pid" 2>/dev/null; do
    local now elapsed
    now="$(date +%s)"
    elapsed=$((now - started_at))
    printf '\r%s %s... elapsed: %02d:%02d' \
      "${frames[$frame]}" "$label" $((elapsed / 60)) $((elapsed % 60))
    frame=$(((frame + 1) % ${#frames[@]}))
    sleep 1
  done

  wait "$command_pid"
  local status=$?

  local finished_at elapsed
  finished_at="$(date +%s)"
  elapsed=$((finished_at - started_at))

  if [[ "$status" -eq 0 ]]; then
    printf '\r✓ %s completed in %02d:%02d%20s\n' \
      "$label" $((elapsed / 60)) $((elapsed % 60)) ""
  else
    printf '\r✗ %s failed after %02d:%02d%20s\n' \
      "$label" $((elapsed / 60)) $((elapsed % 60)) ""
    return "$status"
  fi
}

count_benchmark_cases() {
  docker compose exec -T queryx python - <<'PY'
import json
from pathlib import Path

path = Path("/app/benchmark/cases.json")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("?")
    raise SystemExit(0)

if isinstance(data, list):
    print(len(data))
elif isinstance(data, dict):
    for key in ("cases", "items", "tests"):
        value = data.get(key)
        if isinstance(value, list):
            print(len(value))
            break
    else:
        print("?")
else:
    print("?")
PY
}

choose_action() {
  printf '\nWhat do you want to run?\n' >&2
  printf '  1) Ask a single natural-language question\n' >&2
  printf '  2) Run the complete experimental benchmark\n' >&2
  printf '  3) Exit\n\n' >&2

  while true; do
    read -r -p "Choose [1-3]: " choice

    case "$choice" in
      1|2|3)
        printf '%s\n' "$choice"
        return 0
        ;;
      *)
        printf 'Invalid choice. Enter 1, 2, or 3.\n' >&2
        ;;
    esac
  done
}

run_single_question() {
  local question execute_choice execute_value response

  echo
  echo "Single natural-language query"
  echo "-----------------------------"

  while true; do
    read -r -p "Question: " question
    [[ -n "${question//[[:space:]]/}" ]] && break
    echo "The question cannot be empty."
  done

  read -r -p "Execute the generated plan? [Y/n]: " execute_choice

  case "${execute_choice:-Y}" in
    n|N|no|NO|No)
      execute_value=false
      ;;
    *)
      execute_value=true
      ;;
  esac

  response="$(
    curl -fsS \
      -X POST "${BASE_URL}/query/natural-language" \
      -H 'Content-Type: application/json' \
      --data "$(
        QUESTION="$question" EXECUTE_VALUE="$execute_value" python3 - <<'PY'
import json
import os

print(json.dumps({
    "question": os.environ["QUESTION"],
    "execute": os.environ["EXECUTE_VALUE"].lower() == "true",
}, ensure_ascii=False))
PY
      )"
  )"

  echo
  echo "Response"
  echo "--------"

  if command -v jq >/dev/null 2>&1; then
    printf '%s\n' "$response" | jq
  else
    printf '%s\n' "$response"
  fi
}

run_benchmark() {
  local total_phases=6
  local case_count

  print_phase 1 "$total_phases" "Generating deterministic demo data"
  docker compose exec queryx python -m queryx.tools.seed_demo

  print_phase 2 "$total_phases" "Scanning MySQL source"
  curl -fsS -X POST \
    "${BASE_URL}/sources/${MYSQL_SOURCE_ID}/scan" >/dev/null
  printf 'Scanned source: %s\n' "$MYSQL_SOURCE_ID"

  print_phase 3 "$total_phases" "Scanning MongoDB source"
  curl -fsS -X POST \
    "${BASE_URL}/sources/${MONGODB_SOURCE_ID}/scan" >/dev/null
  printf 'Scanned source: %s\n' "$MONGODB_SOURCE_ID"

  print_phase 4 "$total_phases" "Generating benchmark ground truth"
  run_with_status \
    "Ground-truth generation" \
    docker compose exec queryx python -m benchmark.generate_ground_truth

  print_phase 5 "$total_phases" "Preparing benchmark"
  case_count="$(count_benchmark_cases)"
  printf 'Benchmark cases: %s\n' "$case_count"
  printf 'Model label: %s\n' "$MODEL_LABEL"

  print_phase 6 "$total_phases" "Running the complete benchmark"
  printf 'The runner output is shown below. The status indicator reports elapsed time.\n\n'

  run_with_status \
    "Benchmark" \
    docker compose exec queryx python -u -m benchmark.run \
      --base-url http://127.0.0.1:8000 \
      --cases /app/benchmark/cases.json \
      --output-dir /app/benchmark/results \
      --model-label "$MODEL_LABEL"

  printf '\nReproduction completed successfully.\n'
  printf 'Benchmark label: %s\n' "$MODEL_LABEL"
  printf 'Cases processed: %s\n' "$case_count"
  printf 'Results directory: benchmark/results/\n'
}

require_command docker
require_command curl
require_command ollama
require_command python3

docker compose version >/dev/null 2>&1 \
  || fail "Docker Compose is not available"

if [[ ! -f .env ]]; then
  [[ -f .env.example ]] || fail ".env.example not found"
  cp .env.example .env
  printf 'Created .env from .env.example.\n'
fi

OLLAMA_MODEL="$(read_env_value OLLAMA_MODEL .env || true)"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3.5:9b}"

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
  fail "Ollama is not reachable at http://localhost:11434. Start it with: ollama serve"
fi

if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
  fail "Ollama model '$OLLAMA_MODEL' is not installed. Run: ollama pull $OLLAMA_MODEL"
fi

printf 'Preflight completed. Ollama model: %s\n' "$OLLAMA_MODEL"

if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
  exit 0
fi

if ask_model_warmup; then
  warmup_model
else
  echo "Model warm-up skipped."
fi

docker compose up --build -d
wait_for_api

action="$(choose_action)"

case "$action" in
  1)
    run_single_question
    ;;
  2)
    run_benchmark
    ;;
  3)
    printf 'Operation cancelled.\n'
    ;;
esac
