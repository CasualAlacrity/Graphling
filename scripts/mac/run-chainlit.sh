#!/bin/bash
# Launch the Chainlit chat UI in a browser tab.
# Requires scripts/mac/setup.sh to have been run first.

cd "$(dirname "$0")/../.." || exit 1

if [ ! -x .venv/bin/python3 ]; then
    echo "No virtualenv found. Run scripts/mac/setup.sh first."
    exit 1
fi

if [ ! -f .env ]; then
    echo "No .env file found. Copy your API keys into a .env file in this folder first."
    exit 1
fi

exec .venv/bin/chainlit run app/main.py
