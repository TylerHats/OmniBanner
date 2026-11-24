#!/bin/bash
set -e

if [ ! -d ".venv" ] || [ ! -x ".venv/bin/uvicorn" ]; then
    echo "Initializing virtual environment (.venv)..."
    if ! python3 -m venv .venv 2>/dev/null; then
        python3 -m venv --without-pip .venv
        curl -sSL https://bootstrap.pypa.io/get-pip.py -o get-pip.py
        .venv/bin/python3 get-pip.py
        rm get-pip.py
    fi
    .venv/bin/pip install -r requirements.txt
fi

RELOAD_FLAG=""
if [ "${OMNIBANNER_RELOAD:-0}" = "1" ]; then
    RELOAD_FLAG="--reload"
fi

echo "Starting OmniBanner backend server on port ${PORT:-8000}..."
exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*" $RELOAD_FLAG
