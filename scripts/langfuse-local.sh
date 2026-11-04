#!/usr/bin/env bash
# Run Langfuse locally (self-hosted) so traces never leave this machine.
# Usage: scripts/langfuse-local.sh up | down | status | logs | reset
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$root/infra/langfuse"
env_file="$dir/.env"
app_env="$root/.env"
compose=(docker compose --project-name aisleeye-langfuse --env-file "$env_file" -f "$dir/docker-compose.yml")

secret() { openssl rand -hex "${1:-24}"; }

generate_env() {
  [[ -f "$env_file" ]] && return
  umask 077
  cat >"$env_file" <<EOF
POSTGRES_PASSWORD=$(secret)
CLICKHOUSE_PASSWORD=$(secret)
MINIO_ROOT_PASSWORD=$(secret)
REDIS_AUTH=$(secret)
NEXTAUTH_SECRET=$(secret 32)
SALT=$(secret 32)
ENCRYPTION_KEY=$(secret 32)
LANGFUSE_PUBLIC_KEY=pk-lf-$(uuidgen | tr 'A-Z' 'a-z')
LANGFUSE_SECRET_KEY=sk-lf-$(uuidgen | tr 'A-Z' 'a-z')
LANGFUSE_USER_EMAIL=admin@aisleeye.local
LANGFUSE_USER_PASSWORD=$(secret 12)
EOF
  echo "Generated secrets in $env_file (git-ignored)."
}

# Point the app at the local instance without touching any other line of the root .env.
sync_app_env() {
  touch "$app_env"
  local key value
  for key in LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
    value="$(grep "^$key=" "$env_file" | cut -d= -f2-)"
    set_app_var "$key" "$value"
  done
  set_app_var LANGFUSE_ENABLED true
  set_app_var LANGFUSE_BASE_URL http://localhost:3000
}

set_app_var() {
  if grep -q "^$1=" "$app_env"; then
    sed -i.bak "s|^$1=.*|$1=$2|" "$app_env" && rm -f "$app_env.bak"
  else
    printf '%s=%s\n' "$1" "$2" >>"$app_env"
  fi
}

wait_ready() {
  echo "Waiting for Langfuse (first start runs database migrations, this can take a few minutes)..."
  for _ in $(seq 1 90); do
    if curl -fsS http://localhost:3000/api/public/health >/dev/null 2>&1; then
      echo "Langfuse is up: http://localhost:3000"
      return
    fi
    sleep 4
  done
  echo "Langfuse did not become healthy; check: scripts/langfuse-local.sh logs" >&2
  exit 1
}

case "${1:-}" in
  up)
    generate_env
    "${compose[@]}" pull
    "${compose[@]}" up -d
    sync_app_env
    wait_ready
    echo "Login: $(grep '^LANGFUSE_USER_EMAIL=' "$env_file" | cut -d= -f2-) / password in $env_file"
    ;;
  down) "${compose[@]}" down ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --tail 100 langfuse-web langfuse-worker ;;
  reset)
    "${compose[@]}" down --volumes
    rm -f "$env_file"
    echo "Removed containers, data volumes and generated secrets."
    ;;
  *)
    echo "usage: $0 up|down|status|logs|reset" >&2
    exit 2
    ;;
esac
