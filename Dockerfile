# Image for app/server/ (the ledger API) only — ALICE the desktop client is never
# containerized, it runs on each pilot's own machine. See docs/deploy.md.
#
# Copies the whole app/ tree (matching the repo's own layout, not a curated subset) so
# alembic.ini's `%(here)s/app/db/migrations` script_location and db/migrations/env.py's
# sys.path setup work unmodified — same relative shape as running things locally from
# the repo root. The unused desktop-client code (voice/, overlay/, tools/trade_run/,
# graph.py) just sits there unimported; only server/requirements.txt gets installed, so
# none of LangChain/Whisper/PySide6's dependencies are pulled in.
FROM python:3.12-slim

WORKDIR /srv

COPY app/server/requirements.txt ./server-requirements.txt
RUN pip install --no-cache-dir -r server-requirements.txt

COPY alembic.ini .
COPY app ./app

ENV PYTHONPATH=/srv/app

EXPOSE 8000

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
