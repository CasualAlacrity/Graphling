# Ledger edit path — editing recorded leg values before Finalize

**Status:** proposal, ready to build (2026-09-29). Why this exists and the wider design it
belongs to: `docs/intent/run-model.md`. Tracked in `docs/todo.md` Phase 1 ("URGENT —
recorded leg values can't be edited" → "Ledger edit path — server + overlay").

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

1. **Editable fields:** `quantity_scu`, `price_per_unit`, `cargo_transfer_fee`. All
   optional in the request; at least one required.
2. **Not editable in this slice:** `cargo_transfer_type` — switching manual ↔ autoload
   changes which milestones the leg should have (a manual sale has its own unload step).
   Open question in the intent doc.
3. **Allowed when:** the leg's `transaction_completed_at` is set **and** its run's
   `finalized_at` is not. A leg that's already done (`finalized_at` set on the *leg*) is
   still editable and **stays done** — no timestamp changes on edit.
4. **Rejected (400, via the routes' existing `ValueError` → `HTTPException` pattern) when:**
   no such leg; nothing recorded yet (point the caller at record_purchase/record_sale);
   run already finalized; any negative value; the quantity rule below is violated.
5. **Quantity rule — one shared helper, used by `record_purchase`, `record_sale`, and the
   new update:**
   - Setting the **acquisition** quantity to Q: if the sale leg's transaction isn't
     recorded yet, set the sale leg's planned `quantity_scu` to Q (carry-forward). If it
     is recorded and its quantity > Q, reject ("can't have sold more than was bought").
   - Setting the **sale** quantity to Q: reject if Q > the acquisition leg's quantity.
   - Carry-forward in `record_purchase` is the fix for the existing 614-vs-640 bug. It also
     fixes voice "sold it all", since `mark_cargo_sold` falls back to the leg's planned
     quantity.
6. **Recording a second time stays refused.** `record_purchase`/`record_sale` keep their
   "already recorded" guard — editing is a separate, explicit path, not a re-record.
7. **Ownership:** follows the existing transitive scoping described in
   `ledger_service.py`'s header comment — no per-call user check, same as the other
   id-based endpoints.

## Changes

**Server**
- `ledger_schemas.py`: `UpdateTransactionRequest` — the three optional fields.
- `ledger_service.py`: `update_transaction(leg_id, quantity_scu=None, price_per_unit=None,
  cargo_transfer_fee=None) -> TradeLeg` applying rules 3–5; extract the quantity rule into
  a helper and call it from `record_purchase` / `record_sale` too. Loads the leg's run with
  its legs (`selectinload`, as `finalize_run` does) to check `finalized_at` and the sibling.
- `routes/ledger.py`: `PATCH /trade-runs/legs/{leg_id}/transaction`, `response_model=
  TradeLegOut`, same try/except shape as the neighbouring routes.

**Client**
- `ledger_client.py`: `update_transaction(leg_id, quantity_scu=None, price_per_unit=None,
  cargo_transfer_fee=None) -> TradeLegOut`, sending only the fields given.

**Overlay**
- An **Edit** button on the recap for any leg with a recorded transaction while its run
  isn't finalized — both where the recap appears for non-current legs and on the current
  leg's Mark Done view. The Finalize Run step reaches both legs this way.
- Clicking Edit swaps the recap for the existing `_TransactionWidget` subclass
  (`BuyCargoWidget` / `SellCargoWidget`) in an **edit mode**: prefilled from the recorded
  values, transfer-type toggle disabled, confirm reads "Save changes", plus a Cancel that
  restores the recap. Save calls `ledger_client.update_transaction` then `refresh()`;
  errors go through `show_message`, like the other handlers in `trade_runs_panel.py`.
- Reuse the widget's `on_change` → `_draft_purchase` path so the run header's projected
  investment/profit previews the edit live, as it already does for a fresh purchase.
- Sell form: cap the quantity spinbox at the acquisition leg's quantity.
- **No scrollbars** (hard overlay rule): the edit form replaces the recap in place; it must
  not grow the panel into scrolling.
- Mind the two PySide6 gotchas documented in `_build_run_card` (reparent before
  `setVisible`, and only after children are built).

## Tests

- `tests/server/test_ledger_service.py`:
  - edit a recorded buy leg's price → value changed, timestamps unchanged;
  - edit a done leg → values changed, leg `finalized_at` unchanged;
  - edit on a finalized run → rejected;
  - edit before anything is recorded → rejected;
  - buy quantity edit with sale unrecorded → sale planned quantity follows;
  - buy quantity edit below a recorded sale quantity → rejected;
  - sale quantity above bought → rejected, for both `record_sale` and the edit;
  - `record_purchase` at less than planned → sale leg's planned quantity follows;
  - second `record_purchase` still refused.
- `tests/server/test_ledger_routes.py`: the PATCH route maps `ValueError` → 400 and
  returns the updated leg.
- `tests/overlay/test_trade_runs_panel_render.py`: Edit appears on recorded legs of an
  unfinalized run; absent on unrecorded legs and on finalized runs; edit mode prefills
  the recorded values.

## Out of scope (separate items in `docs/todo.md`)

- Voice correction tool (Jeff's).
- N sale legs, remainder display, Finalize requiring remainder = 0, write-off, sell-only
  runs — `docs/intent/run-model.md`.
- Editing transfer type.
