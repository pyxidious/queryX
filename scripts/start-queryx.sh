#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
START_OLLAMA="${START_OLLAMA:-true}"
WAIT_ATTEMPTS="${WAIT_ATTEMPTS:-60}"
WAIT_SECONDS="${WAIT_SECONDS:-2}"

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || fail "Required command not found: $1"
}

wait_for_url() {
  local url="$1"
  local label="$2"

  printf 'Waiting for %s...\n' "$label"

  for _ in $(seq 1 "$WAIT_ATTEMPTS"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      printf '✓ %s is ready.\n' "$label"
      return 0
    fi

    sleep "$WAIT_SECONDS"
  done

  fail "$label did not become ready in time"
}

start_ollama_if_needed() {
  if curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    printf '✓ Ollama is already running.\n'
    return 0
  fi

  if [[ "$START_OLLAMA" != "true" ]]; then
    fail "Ollama is not running at ${OLLAMA_URL}"
  fi

  printf 'Starting Ollama...\n'

  nohup ollama serve \
    > /tmp/queryx-ollama.log \
    2>&1 &

  printf '%s\n' "$!" > /tmp/queryx-ollama.pid

  wait_for_url \
    "${OLLAMA_URL}/api/tags" \
    "Ollama"
}

require_command docker
require_command curl
require_command ollama

docker compose version >/dev/null 2>&1 \
  || fail "Docker Compose is not available"

printf '\nStarting QueryX\n'
printf '%s\n' '---------------'

start_ollama_if_needed

printf 'Starting Docker services...\n'
docker compose up --build -d

wait_for_url \
  "${BASE_URL}/health" \
  "QueryX API"

printf '\nQueryX is ready.\n'
printf 'Dashboard: %s/ui\n' "$BASE_URL"
printf 'Query UI:  %s/ui/query\n' "$BASE_URL"
printf 'API docs:  %s/docs\n' "$BASE_URL"
printf '\nContainer status:\n'

docker compose ps