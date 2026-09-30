# Ledger edit path — editing recorded leg values before Finalize

**Status:** built (2026-09-29) — server, client, overlay, 30 new tests, full suite
passing. Not yet verified against a live server/Postgres/overlay run. Two things below
changed from this doc's original proposal while scoping the actual build: transfer type
turned out safe to include (see Rules), and the quantity cross-check in Rule 5 was
dropped entirely (see `docs/intent/run-model.md`'s "selling more than was bought is
allowed" decision) — this doc's body is updated to match what was actually built, not
left as the original proposal. Why this exists and the wider design it belongs to:
`docs/intent/run-model.md`. Tracked in `docs/todo.md` Phase 1 ("URGENT — recorded leg
values can't be edited" → "Ledger edit path — server + overlay").

**Owner:** Claude (server + overlay, per the work split in `docs/todo.md`'s header). The
voice correction tool that will sit on top of this is Jeff's and is **out of scope here**.

## Goal

A pilot can change the quantity, price, or fee of any leg whose purchase/sale has been
recorded, at any point until the run is finalized — including legs already marked done.
Also fix two related quantity bugs that the edit path would otherwise inherit.

## Current behaviour (verified 2026-09-29)

- `app/server/ledger_service.py`: `record_purchase` / `record_sale` raise if
  `transaction_completed_at` is already set. No update function exists.
- `app/server/routes/ledger.py`: no update route.
- `app/overlay/trade_run_widgets.py`: `build_recap_grid` is a read-only summary, used for
  non-current legs and for the current leg's final "Mark Done" view
  (`trade_runs_panel.py::_build_leg_dialog`'s fallthrough). No edit affordance anywhere.
- `_apply_transaction` only writes its own leg — buying 614 on a 640 plan leaves the sale
  leg planned at 640.
- Nothing prevents a sale quantity larger than the purchased quantity (Sell spinbox max is
  100,000; server never compares legs).

## Rules

1. **Editable fields:** `quantity_scu`, `price_per_unit`, `cargo_transfer_fee`,
   `cargo_transfer_type`. All optional in the request; `None` means unchanged.
2. **Transfer type is editable** (reversed from this doc's original exclusion, decided
   while scoping the build): switching manual ↔ autoload on an already-recorded leg
   turned out safe post-hoc, because `transferred_at` is already stamped by record time
   regardless of transfer type (`record_sale`'s existing
   `also_stamp_transferred=leg.transferred_at is None` logic guarantees this) — no
   milestone needs to be retroactively added or removed, it's a value+fee change only.
3. **Allowed when:** the leg's `transaction_completed_at` is set **and** its run's
   `finalized_at` is not. A leg that's already done (`finalized_at` set on the *leg*) is
   still editable and **stays done** — no timestamp changes on edit.
4. **Rejected (400, via the routes' existing `ValueError` → `HTTPException` pattern) when:**
   no such leg; nothing recorded yet (point the caller at record_purchase/record_sale);
   run already finalized; any negative value.
5. **No cross-check between a run's own acquisition and sale quantity** (reversed from
   this doc's original "reject if sale exceeds bought" — decided while scoping the
   build, see `docs/intent/run-model.md`): selling more than a run's own acquisition leg
   is a real case, cargo sold from inventory carried over from earlier play (buy 640,
   sell 700), not a bug to reject. Buy and sell quantities are independent, pilot-trusted
   facts. **Carry-forward stays as a default, not a constraint:** `record_purchase` sets
   the sale leg's *planned* `quantity_scu` to match the acquisition, but only while the
   sale isn't yet recorded — fixes the 614-vs-640 bug and voice "sold it all"
   (`mark_cargo_sold` falls back to the leg's planned quantity), without blocking a later
   sale quantity that legitimately differs.
6. **Recording a second time stays refused.** `record_purchase`/`record_sale` keep their
   "already recorded" guard — editing is a separate, explicit path, not a re-record.
7. **Ownership:** follows the existing transitive scoping described in
   `ledger_service.py`'s header comment — no per-call user check, same as the other
   id-based endpoints.

## Changes

**Server**
- `ledger_schemas.py`: `UpdateTransactionRequest` — the four optional fields.
- `ledger_service.py`: `update_transaction(leg_id, quantity_scu=None, price_per_unit=None,
  cargo_transfer_fee=None, cargo_transfer_type=None) -> TradeLeg` applying rules 3–5.
  `record_purchase` gained the carry-forward addition (rule 5); no shared cross-check
  helper exists since there's no cross-check to share.
- `routes/ledger.py`: `PATCH /trade-runs/legs/{leg_id}/transaction`, `response_model=
  TradeLegOut`, same try/except shape as the neighbouring routes.

**Client**
- `ledger_client.py`: `update_transaction(leg_id, quantity_scu=None, price_per_unit=None,
  cargo_transfer_fee=None, cargo_transfer_type=None) -> TradeLegOut`, sending only the
  fields given.

**Overlay**
- An **Edit** button on the recap for any leg with a recorded transaction while its run
  isn't finalized — both where the recap appears for non-current legs and on the current
  leg's Mark Done view. The Finalize Run step reaches both legs this way.
- Clicking Edit swaps the recap for the existing `_TransactionWidget` subclass
  (`BuyCargoWidget` / `SellCargoWidget`) in an **edit mode**: prefilled from the recorded
  values, transfer-type toggle **stays enabled** (reuses the existing
  `_wire_signals`/`_update_fee_visibility` logic a fresh form already has, no
  special-casing), confirm reads "Save changes", plus a Cancel that restores the recap.
  Save calls `ledger_client.update_transaction` then `refresh()`; errors go through
  `show_message`, like the other handlers in `trade_runs_panel.py`.
- Reuse the widget's `on_change` → `_draft_purchase` path so the run header's projected
  investment/profit previews the edit live, as it already does for a fresh purchase.
- No spinbox cap on the Sell form (the original proposal's cap-at-acquisition idea is
  gone along with the quantity cross-check) — same 0–100,000 range as Buy.
- **No scrollbars** (hard overlay rule): the edit form replaces the recap in place; it must
  not grow the panel into scrolling.
- Mind the two PySide6 gotchas documented in `_build_run_card` (reparent before
  `setVisible`, and only after children are built).

## Tests

- `tests/server/test_ledger_service.py`:
  - edit a recorded leg's price/quantity → value changed, timestamps unchanged;
  - edit a done leg → values changed, leg `finalized_at` unchanged;
  - edit on a finalized run → rejected;
  - edit before anything is recorded → rejected;
  - edit rejects negative values;
  - edit a leg's transfer type → value changed, `transferred_at` unchanged;
  - buy quantity edit with sale unrecorded → sale's planned quantity follows;
  - `record_purchase` at less than planned → sale leg's planned quantity follows;
  - sale quantity above bought → **allowed**, not rejected (regression guard);
  - buy quantity edit below an already-recorded sale quantity → **allowed** (regression guard);
  - second `record_purchase` still refused.
- `tests/server/test_ledger_routes.py`: the PATCH route maps `ValueError` → 400 and
  returns the updated leg.
- `tests/overlay/test_trade_runs_panel_render.py`: Edit appears on recorded legs of an
  unfinalized run; absent on unrecorded legs and on finalized runs; edit mode prefills
  the recorded values (including transfer type); Cancel restores the recap.

## Out of scope (separate items in `docs/todo.md`)

- Voice correction tool (Jeff's).
- N sale legs, remainder display, Finalize requiring remainder = 0, write-off, sell-only
  runs — `docs/intent/run-model.md`.
