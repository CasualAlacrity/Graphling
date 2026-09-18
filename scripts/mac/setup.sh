#!/bin/bash
# One-time setup for ALICE on macOS.
# Creates a Python 3.11 virtualenv and installs dependencies.

cd "$(dirname "$0")/../.." || exit 1

if [ ! -x .venv/bin/python3 ]; then
    echo "Creating virtualenv with Python 3.11..."
    if ! python3.11 -m venv .venv; then
        echo
        echo "ERROR: could not create the venv with Python 3.11."
        echo "Check that Python 3.11 is installed: python3.11 --version"
        exit 1
    fi
else
    echo "Virtualenv already exists, reusing it."
fi

echo "Installing dependencies (this can take a few minutes the first time)..."
.venv/bin/python -m pip install --upgrade pip
# Editable install of the project itself (pyproject.toml), which also pulls in
# everything from requirements.txt and registers the `alice` command used by the
# run script below.
if ! .venv/bin/python -m pip install -e .; then
    echo
    echo "ERROR: dependency install failed. Scroll up for the offending package."
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo
    echo "WARNING: Docker not found on PATH. Install Docker Desktop, then run"
    echo "  docker compose up -d"
    echo "yourself before using the trade route tracker or overlay."
else
    echo "Starting Postgres (trade route tracker storage)..."
    if ! docker compose up -d --wait; then
        echo
        echo "WARNING: docker compose up failed. Is Docker Desktop running?"
    else
        echo "Applying database migrations..."
        if ! .venv/bin/python -m alembic upgrade head; then
            echo
            echo "WARNING: alembic upgrade failed. Scroll up for the error."
        fi
    fi
fi

echo
echo "Setup complete."
echo "Make sure a .env file with your API keys exists in this folder before running."
echo "Run scripts/mac/run-alice.sh to start ALICE (voice + overlay together)."
