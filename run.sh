#!/usr/bin/env bash
# ============================================================================
#  microCT Segmentation Lab — Linux/macOS launcher (portable)
#  First run creates a local .venv and installs the app. Needs internet once.
# ============================================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[setup] creating virtual environment..."
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  echo "[setup] installing app (dashboard)..."
  ./.venv/bin/pip install -e .
  echo
  echo "[note] To run segmentation on THIS machine, also: ./.venv/bin/pip install -e '.[seg]'"
  echo "       (install a CUDA torch build first for GPU speed.)"
  echo
fi

[ -f .env ] || cp .env.example .env

echo "[start] launching job worker..."
./.venv/bin/microct-worker &
WORKER_PID=$!
trap "kill $WORKER_PID 2>/dev/null" EXIT

sleep 2
echo "[start] open http://127.0.0.1:8000"
./.venv/bin/microct-web
