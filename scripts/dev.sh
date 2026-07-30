#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$ROOT/frontend"

cleanup() {
  echo ""
  echo "Shutting down..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
  wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
  echo "Done."
}
trap cleanup EXIT INT TERM

echo "=== Starting PR-Agent backend (FastAPI) ==="
cd "$ROOT"
.venv/bin/uvicorn pr_agent.servers.prompting_server:app --host 0.0.0.0 --port 8090 --reload &
BACKEND_PID=$!

echo "=== Starting PR-Agent frontend (Next.js) ==="
cd "$FRONTEND_DIR"
npm run dev -- --port 3000 &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:8090"
echo "Frontend: http://localhost:3000"
echo "Press Ctrl+C to stop both."
echo ""

wait
