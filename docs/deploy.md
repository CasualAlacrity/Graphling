# Deploying the ledger server

Only `app/server/` (the ledger API) is ever deployed. ALICE — the voice loop, the
LangGraph agent, the overlay — is never containerized; it runs on each pilot's own
machine and talks to whichever server their `ALICE_API_URL` points at. See
`docs/todo.md`'s Phase 2 section for why this shape replaced direct-DB client access.

## Where it lives

Same shared Hetzner box as Flockt (`168.119.234.226`), its own docker-compose stack
under `/opt/graphling`, isolated from Flockt's `/opt/flockt` — a separate Postgres, a
separate container, no shared state between the two apps. `docker-compose.prod.yml` at
the repo root is what gets copied there (as `docker-compose.yml`) on every deploy.

Postgres itself has **no published port** — same as Flockt's own `db` service. Once a
server mediates every ledger read/write, nothing outside this box ever needs to reach
Postgres directly, so there's no VPN/Tailscale/firewall story to maintain for it at all.
The `server` container publishes only to `127.0.0.1:8090` — nginx (already running on
this box) is the sole path in, proxying `api.heyalice.help` to it, the same shape as the
existing `app.flockt.farm` → `127.0.0.1:8088` site.

## One-time setup on the box

1. **DNS first, before certbot.** Add an `A` record for `api.heyalice.help` pointing at
   `168.119.234.226` (and an `AAAA` record too if the box has an IPv6 address) at
   whatever DNS provider hosts `heyalice.help`. Let's Encrypt verifies domain ownership
   by actually reaching the domain — certbot fails with a `DNS problem: NXDOMAIN` error
   until this resolves. Propagation is usually minutes, sometimes longer depending on
   the provider's TTL; `dig api.heyalice.help` should return the box's IP before moving
   on to step 2.

2. **nginx + certbot site for `api.heyalice.help`** (that domain is already live on this
   box for the static landing page — this adds an API subdomain alongside it):
   ```bash
   sudo tee /etc/nginx/sites-available/api.heyalice.help <<'EOF'
   server {
       server_name api.heyalice.help;
       location / {
           proxy_pass http://127.0.0.1:8090;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto https;
       }
       listen 80;
   }
   EOF
   sudo ln -s /etc/nginx/sites-available/api.heyalice.help /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d api.heyalice.help
   ```

3. **Discord Developer Portal** (discord.com/developers/applications), manual, only the
   account owner can do this:
   - Turn **"Public Client" OFF** — the server now holds a real client secret (the old
     client-side PKCE flow needed it on; this replaced that, see
     `auth/discord_identity.py`'s docstring).
   - Copy the resulting **Client Secret**.
   - Add a **Redirect URI**: `https://api.heyalice.help/auth/discord/callback`.

4. **`/opt/graphling/.env`** on the box (not committed — create it directly there):
   ```
   POSTGRES_PASSWORD=<generate one>
   DISCORD_CLIENT_ID=<from the portal>
   DISCORD_CLIENT_SECRET=<from step 3>
   DISCORD_REDIRECT_URI=https://api.heyalice.help/auth/discord/callback
   JWT_SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
   ```
   `docker-compose.prod.yml` reads these via `${VAR}` substitution from this file.

## Deploying

```bash
./scripts/deploy_server.sh              # build, push, deploy
./scripts/deploy_server.sh --no-registry  # skip Docker Hub, stream the image over SSH
./scripts/deploy_server.sh --migrate    # also run alembic upgrade head after deploying
```

Modeled directly on `chicken-tracker/deploy-prod.sh` (same author, same box) — builds
the image from the repo-root `Dockerfile`, tags it with the short git SHA, ships it to
the server, rewrites `docker-compose.prod.yml`'s image tag, copies it over, and
recreates the `server`/`postgres` containers.

**Migrations are a separate, explicit step** (`--migrate`, or run by hand:
`ssh ... "cd /opt/graphling && docker compose run --rm server alembic upgrade head"`) —
deliberately not switched to auto-migrate-on-boot (Flockt's own pattern): this project's
Alembic history is already established, tested infrastructure, and a schema change
landing silently on container start is a worse failure mode here than one extra command.

## Verifying a deploy

```bash
curl https://api.heyalice.help/health
ssh -i ~/.ssh/flockt root@168.119.234.226 "cd /opt/graphling && docker compose logs --tail=40 server"
```

Walk through `/auth/discord/login` in a real browser (with a real pilot's Discord
account) before pointing any real client's `ALICE_API_URL` at the new deploy — the
Dockerfile and alembic setup were verified locally against a real Postgres before this
was ever written up, but the live Discord round trip only exists in production.

## Rollback

The previous image is still on Docker Hub under its own SHA tag:
```bash
ssh -i ~/.ssh/flockt root@168.119.234.226
cd /opt/graphling
sed -i "s|image: feenstra32/graphling-server:.*|image: feenstra32/graphling-server:<previous-sha>|" docker-compose.yml
docker compose pull server && docker compose up -d --force-recreate server
```

Schema changes do not roll back — every migration so far is additive (new tables, new
columns), so an older image runs fine against a newer database. A migration that ever
drops or renames something breaks this assumption; take a Postgres backup first if so.

## Local dev

Run the server locally instead of deploying, against the existing local docker-compose
Postgres:
```bash
docker compose up -d postgres          # the existing local docker-compose.yml, unchanged
cd app && ../.venv/bin/uvicorn server.main:app --reload
```
Point ALICE's own `.env` at it with `ALICE_API_URL=http://localhost:8000` (the
`.env-template` default). This is deliberate, not just convenient — it means server
changes (the auth relay, the ledger routes) get exercised locally before they ever run
somewhere a real pilot depends on; production only changes on an explicit
`./scripts/deploy_server.sh` run, never as a side effect of local development.
