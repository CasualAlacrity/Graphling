#!/bin/bash
# Launch the ledger server (app/server/) locally for development — see docs/deploy.md's
# "Local dev" section for why this, not direct-DB access, is the dev path too.
#
# Needs the local docker-compose Postgres already running:
#   docker compose up -d postgres
#
# Then point ALICE's own .env at it (the .env-template default already does this):
#   ALICE_API_URL=http://localhost:8000

cd "$(dirname "$0")/../.." || exit 1

if [ ! -x .venv/bin/python3 ]; then
    echo "No virtualenv found. Run scripts/mac/setup.sh first."
    exit 1
fi

if [ ! -f .env ]; then
    echo "No .env file found — see server/.env-template for what the server itself needs"
    echo "(TRADE_DB_URL, DISCORD_CLIENT_ID/SECRET, JWT_SECRET_KEY); in local dev these"
    echo "just join the same .env as ALICE's own client config."
    exit 1
fi

echo "Starting the ledger server on http://localhost:8000 ...  (Ctrl+C to stop)"
cd app || exit 1
exec ../.venv/bin/uvicorn server.main:app --reload
