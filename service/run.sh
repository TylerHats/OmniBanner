#!/bin/bash
# Local startup script for OmniBanner central service

# Verify virtual environment
if [ ! -d ".venv" ]; then
    echo "Virtual environment (.venv) not found. Setting up..."
    if python3 -m venv .venv 2>/dev/null; then
        echo "Created virtual environment."
    else
        echo "Python ensurepip missing. Bootstrapping virtual environment..."
        python3 -m venv --without-pip .venv
        curl -sSL https://bootstrap.pypa.io/get-pip.py -o get-pip.py
        .venv/bin/python3 get-pip.py
        rm get-pip.py
    fi
    .venv/bin/pip install -r requirements.txt
fi

echo "Starting OmniBanner backend server..."
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
