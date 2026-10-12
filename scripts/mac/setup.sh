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

if [ ! -f .env ]; then
    echo "Creating .env from the pilot settings (.env-pilot)..."
    cp .env-pilot .env
else
    echo ".env already exists, leaving it as it is."
fi

# No database here: the client talks only to ALICE's server, which owns Postgres. A
# developer running a local ledger server follows the README instead.

echo
echo "Setup complete."
echo "Run scripts/mac/run-alice.sh to start ALICE (voice + overlay together)."
echo "The first launch opens your browser to sign in with Discord."
