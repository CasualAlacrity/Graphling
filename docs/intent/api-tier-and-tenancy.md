# Intent — server tier and multi-tenancy

**Status:** not started (2026-09-18). This is the structural blocker between today and a
paying user. Supersedes the shape sketched in `todo.md` Phase 2, which described a
two-friends setup rather than a product.

## Problem

The client is currently the entire system. The desktop app holds the UEX API key, the
ElevenLabs key, the LLM provider config, **and a direct Postgres connection string**.

That breaks three ways at once for anyone who isn't already trusted:

1. Shipping the app means shipping credentials — or asking each user to acquire five API
   keys of their own, which is not a product.
2. A direct database connection from the client means every user can read and write every
   other user's data. **Adding `user_id` columns does not fix this** — the trust boundary
   is in the wrong place, not the schema.
3. Postgres would have to be internet-exposed to serve remote users.

`todo.md` Phase 2's "one shared Postgres both pilots reach" is a LAN-with-friends design.
It's necessary work but nowhere near sufficient.

## Desired outcome

- **Hetzner** hosts Postgres and an API service. Always-on, stable IP, already paid for
  (existing small instance, comfortably under free credit at this scale).
- **Clients** hold a login token and nothing else. No API keys, no connection strings.
- **FrankenLab** is an inference endpoint the API calls over a private tunnel — not a
  public service, not the thing users connect to.

## Decided

- **API tier lives on Hetzner, not FrankenLab.** Ranked provider fallback has to run
  somewhere that's up when FrankenLab isn't; putting it on the home box defeats it.
- **Tailscale/WireGuard between Hetzner and FrankenLab**, not port-forwarding. No dynamic
  DNS, nothing exposed. The extra WAN hop is tens of milliseconds against multi-second
  inference — noise.
- **Cache and reference tables stay global**, no tenant column. Shared economy data is the
  entire point.
- **Runs stay scoped to their owner.** No cross-pilot visibility for now; group/crewed
  runs and org leaderboards remain deferred, with only the schema hook landing here.

## Constraints

- The Hetzner box stops being a hobby machine the moment it holds other people's ledgers.
  Needs real backups (snapshots plus periodic `pg_dump` off-box), and discipline about
  co-tenancy with the chicken tracker — the risk isn't Docker isolation, it's rebooting
  the host for unrelated reasons while a pilot is mid-run.
- `db/session.py`'s NullPool + multi-event-loop rationale exists because the overlay's
  qasync loop and the voice thread share one engine **in the client**. Moving DB access
  behind an API changes those assumptions; re-read that comment before assuming it still
  applies.
- The overlay currently reads the database directly. Every one of those reads becomes an
  API call, which is a larger change than it looks.
- Cash-constrained: prefer the cheap instance and defer anything that adds monthly cost.

## Open questions

- **Auth mechanism.** Needs to survive a desktop client with no browser redirect story —
  device-code flow, long-lived tokens, or something simpler given the seed-user scale.
- **Billing and entitlement.** Subscription implies accounts, payment, and a check at
  request time. Unscoped. Also gated on the legal questions (CIG ToS on monetised tools,
  UEX terms on redistributing their data, ElevenLabs commercial-use terms) — those are
  revenue blockers, not engineering ones, and should resolve before this is built.
- **Migration for existing local data.** Jeff's own trade history lives in a local
  Postgres today. Move it, or start clean?
- **How much of the client stays offline-capable.** If the API is unreachable mid-run, can
  a pilot still advance milestones locally and sync later, or does the run stall?
