#!/usr/bin/env bash
# macOS / Linux 실행 스크립트
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  echo "[setup] creating virtual environment (.venv)"
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
OPEN_BROWSER=1 exec .venv/bin/python -m server.main
