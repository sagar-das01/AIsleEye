#!/usr/bin/env bash
# Start the backend and the frontend for the demo, run the pre-demo checks and print the URLs.
# Usage: scripts/demo.sh [live|replay] [--skip-checks]
#   live    (default) real models; needs MODEL_API_KEY in the git-ignored .env
#   replay  saved results only: no key, no network, nothing leaves this machine
# Ctrl-C stops both servers. No secrets are read or printed here.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
mode="live"
skip_checks=0
for arg in "$@"; do
  case "$arg" in
    live | replay) mode="$arg" ;;
    --skip-checks) skip_checks=1 ;;
    *)
      echo "usage: $0 [live|replay] [--skip-checks]" >&2
      exit 2
      ;;
  esac
done

backend_port=8000
frontend_port=3001
python="$root/backend/.venv/bin/python"
log_dir="${TMPDIR:-/tmp}/aisleeye-demo"
mkdir -p "$log_dir"

fail() { echo "error: $*" >&2; exit 1; }

[[ -x "$python" ]] || fail "backend virtualenv missing: cd backend && python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt"
[[ -d "$root/frontend/node_modules" ]] || fail "frontend dependencies missing: cd frontend && npm install"

if [[ "$mode" == "live" ]]; then
  has_key=0
  [[ -n "${MODEL_API_KEY:-}" ]] && has_key=1
  grep -qE '^MODEL_API_KEY=.+' "$root/.env" 2>/dev/null && has_key=1
  [[ $has_key -eq 1 ]] || fail "no MODEL_API_KEY in the environment or .env; use '$0 replay' to run without one"
fi

for port in "$backend_port" "$frontend_port"; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $port is already in use; stop whatever is running there first"
  fi
done

if [[ $skip_checks -eq 0 ]]; then
  echo "Running the pre-demo checks..."
  check_args=()
  [[ "$mode" == "live" ]] && check_args+=(--live)
  # ${arr[@]+...} keeps an empty array safe under `set -u` on the bash 3.2 that macOS ships.
  (cd "$root/backend" && "$python" -m scripts.predemo ${check_args[@]+"${check_args[@]}"}) || fail "pre-demo checks failed (see above); '$0 $mode --skip-checks' starts anyway"
fi

pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "$mode" == "replay" ]]; then export DEMO_MODE=replay; fi
# The local launcher opts in to credential-free persona sign-in explicitly (an exported value still wins), and the
# server listens on loopback only: persona selection is not identity verification.
export DEMO_SIGN_IN_ENABLED="${DEMO_SIGN_IN_ENABLED:-true}"
(cd "$root/backend" && exec "$python" -m uvicorn app.main:app --host 127.0.0.1 --port "$backend_port" >"$log_dir/backend.log" 2>&1) &
pids+=($!)

build_id="$root/frontend/.next/BUILD_ID"
if [[ ! -f "$build_id" ]] || [[ -n "$(find "$root/frontend/src" "$root/frontend/package.json" "$root/frontend/next.config.ts" -newer "$build_id" -print -quit)" ]]; then
  echo "Building the frontend (first run, or the source changed)..."
  # No NEXT_PUBLIC_API_URL: the browser must use the same-origin /api proxy so the session cookie works.
  (cd "$root/frontend" && BACKEND_URL="http://127.0.0.1:$backend_port" npm run build >"$log_dir/frontend-build.log" 2>&1) \
    || fail "frontend build failed; see $log_dir/frontend-build.log"
fi
(cd "$root/frontend" && exec npm run start >"$log_dir/frontend.log" 2>&1) &
pids+=($!)

wait_for() {
  for _ in $(seq 1 60); do
    curl -fsS "$1" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
wait_for "http://localhost:$backend_port/health" || fail "backend did not start; see $log_dir/backend.log"
wait_for "http://localhost:$frontend_port" || fail "frontend did not start; see $log_dir/frontend.log"

echo
echo "AIsleEye is running ($mode mode)"
echo "  Dashboard: http://localhost:$frontend_port"
echo "  API:       http://localhost:$backend_port/docs"
[[ "$mode" == "replay" ]] && echo "  Replay mode: results are saved ones and the dashboard says so."
echo "  Sign-in:   demo personas (DEMO_SIGN_IN_ENABLED=$DEMO_SIGN_IN_ENABLED); identities are not verified, local synthetic data only."
echo "  Logs:      $log_dir"
echo "Press Ctrl-C to stop."
wait
