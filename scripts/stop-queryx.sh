#!/usr/bin/env bash
set -Eeuo pipefail

STOP_OLLAMA="${STOP_OLLAMA:-false}"
REMOVE_VOLUMES="${REMOVE_VOLUMES:-false}"

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 \
    || fail "Required command not found: $1"
}

stop_ollama() {
  local pid_file="/tmp/queryx-ollama.pid"

  if [[ -f "$pid_file" ]]; then
    local ollama_pid
    ollama_pid="$(cat "$pid_file")"

    if kill -0 "$ollama_pid" 2>/dev/null; then
      printf 'Stopping Ollama started by QueryX...\n'
      kill "$ollama_pid"

      for _ in $(seq 1 10); do
        if ! kill -0 "$ollama_pid" 2>/dev/null; then
          break
        fi

        sleep 1
      done

      if kill -0 "$ollama_pid" 2>/dev/null; then
        printf 'Ollama did not stop gracefully. Forcing shutdown...\n'
        kill -9 "$ollama_pid"
      fi
    fi

    rm -f "$pid_file"
    printf '✓ Ollama stopped.\n'
    return 0
  fi

  printf 'No QueryX-managed Ollama process found.\n'
}

require_command docker

docker compose version >/dev/null 2>&1 \
  || fail "Docker Compose is not available"

printf '\nStopping QueryX\n'
printf '%s\n' '---------------'

if [[ "$REMOVE_VOLUMES" == "true" ]]; then
  printf 'Stopping containers and removing volumes...\n'
  docker compose down --volumes --remove-orphans
else
  printf 'Stopping containers...\n'
  docker compose down --remove-orphans
fi

if [[ "$STOP_OLLAMA" == "true" ]]; then
  stop_ollama
else
  printf 'Ollama left running.\n'
fi

printf '\n✓ QueryX stopped successfully.\n'