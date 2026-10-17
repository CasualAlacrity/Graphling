---
name: alice-design
description: Visual design system for ALICE, Jeff's Star Citizen voice copilot (PySide6/Qt desktop client). Covers the Argo-orange palette, Rajdhani + JetBrains Mono type, Cargo Manifest cards, pulse-glyph volatility icons, and the three surfaces — the F3 workbench (Route Search, In Progress, Ledger), the in-game presence layer (mini tracker, voice states, option set, route card, timer), and the launcher — plus aUEC/SCU unit rules and Qt/QSS handoff. Use for ANY ALICE UI work: mockups, wireframes, style options, new screens, widgets, overlays, panels, dialogs, list rows, icons, color or QSS questions, or implementing UI in app/overlay or app/launcher — even if the request only says "the overlay", "the launcher", "the workbench", "trade routes", "the tracker", or names one component without mentioning design.
---

# ALICE Design System

ALICE is a Star Citizen voice copilot; the desktop client is Python + PySide6. Its UI reads as a ship's instrument panel: near-black surfaces, industrial Argo orange as the action color, monospace for every number, rounded Cargo Manifest cards. The same language covers three surfaces that stay **separate windows with separate lifecycles**:

| Surface | What | Code |
|---|---|---|
| **Workbench** | F3 toggle, 60% of screen width, tabs: Route Search / In Progress / Ledger. For planning. | `app/overlay/` |
| **Presence layer** | Small in-game strip: mini tracker, voice states, option set, route card, timer. Glanceable mid-flight. *Designed, not built.* | — (see `docs/presence-layer.md`) |
| **Launcher** | 480 px front door: sign in, controls, start/stop, startup progress. Closing it quits ALICE. | `app/launcher/` |

Token values live in `app/overlay/theme.py` (the source of truth in code) and `references/tokens.md`.

## How Jeff works on design

- **Mockups first, as a static HTML page** in `docs/wireframes/`, shown with `SendUserFile` (`display: "render"`) — never assume Jeff can see a file you only opened in your own browser pane.
- **Show 2–3 genuinely different options** where a choice is open, each with a one-line honest trade-off, and recommend one. Options differ in approach, not just color. When a direction is already settled, just apply it.
- **Draw real states, not one happy path** — e.g. a run mid-leg *and* ready to finalize; the launcher signed-out, starting, live, failed. Check the code for what states actually exist before drawing.
- **Realistic Star Citizen data:** real terminals (Area18 TDD, ArcCorp Mining Area 045, Shubin Mining SMO-10, Port Tressler, Baijini Point, Lorville, Grim HEX, Orison), real commodities (Laranite, Agricium, Titanium, Medical Supplies), Stanton/Pyro. Per-SCU prices in the thousands for ores.
- **Jeff reviews, Claude writes the code in every layer** (UI included) once a mockup is agreed. Bring a panel toward this spec when you're touching it for a requested change; don't restyle untouched panels unasked.
- Flag Qt gotchas briefly when a design choice is cheap in CSS but awkward in QSS (`references/qt-notes.md`).

## Core rules

1. **Orange means "do this now".** `#F2650C` is for the primary action, focus, the active tab, and the **3px orange edge on whichever card needs the pilot right now** (at most one per view; none when nothing is pending, e.g. the launcher while live). Route sections (Source/Destination) also carry the orange edge — they *are* the route.
2. **Green means finished, red means failed/loss, amber means caution.** Green: done steps, finalized legs, positive profit, "signed in", live, and the one green button — **Finalize Run** (the sign-off moment). Never use status colors for decoration or categories (no blue/green buy/sell badges). Blue (`info`) is backdrop glow only.
3. **Never encode meaning by color alone.** Every state has a shape or word: pulse silhouettes, ↓/↑ on BUY/SELL badges, signed profit (`+217,580`), chips with glyph + word (`✓ signed in`, `✕ stopped`). This is why the launcher's old colored status dots are replaced.
4. **Numbers are monospace, names are Rajdhani.** JetBrains Mono for every value, price, quantity, code, breadcrumb, hotkey, eyebrow. Rajdhani for titles, terminal/commodity names, buttons. System sans for field labels, helper text, and sentences.
5. **Units always inline:** `2,735 aUEC`, `576 SCU`, `415,800 aUEC/h`. Unit smaller and dimmer than the number. Never rely on a column header alone.
6. **Cargo Manifest cards** group content: 8px radius, 1px `border-subtle`, 3px left edge — neutral `border` by default, orange per rule 1, green for a ready summary, red for an error card.
7. **The route is visible.** Origin → destination reads left-to-right (rows) or top-to-bottom (filter, with the ROUTE connector between Source and Destination).
8. **Locations carry breadcrumbs** in 10px mono `text-disabled` with the upper level bolded in `text-secondary` (`ArcCorp › Wala`). In an input, the breadcrumb is a right-aligned suffix inside the field.
9. **Profit is the loudest number; SCU is not a choice factor.** SCU is shown dimmer/smaller than profit everywhere, and moved under the commodity code in compact rows — a bigger number reads as "better" and would argue against the ranking.
10. **Information, not decisions.** No surface commits anything for the pilot: no auto-accept, no auto-finalize. Finalize Run appears only once every leg is finalized. Actions the pilot can't take yet are absent or explained ("Sign in first"), not silently greyed.
11. **Scrolling:** never horizontal, anywhere (`ScrollBarAlwaysOff`). Vertical only on lists that genuinely grow — Route Search results, In Progress runs, Ledger. Never in the presence layer or the launcher: cap, collapse or summarise instead (e.g. launcher Controls collapse to one line while running).
12. **Presence layer is glanceable:** non-modal, never takes focus from the game, fades on its own, read in a second.
13. **Icons are stroke-only SVG** (24px viewBox, ~1.6 stroke, `currentColor`), self-authored. No emoji in UI (they render inconsistently in Qt on Windows) — ✔️/ℹ️/❗ are drawn as check / circle-i / triangle-! glyphs.
14. **Corner brackets** (orange, 14px) only on the workbench root window. Infrastructure problems are shown as UI, never voiced by ALICE.

## Components

Full specs in `references/components.md`; live markup in `assets/alice-style-merge.html` (workbench + presence layer) and `assets/alice-launcher.html` (launcher). Copy markup from those rather than reinventing it.

- **Workbench:** tabs · filter cards (Ship & Cargo / Source / ROUTE / Destination) · icon-only option toggles with tooltips · results row · volatility pulse (thresholds 3% / 8%, plus "no history") · Profit/Margin switch · run card · BUY/SELL badges · milestone stepper · upcoming-finalize row · run summary + Finalize Run.
- **Presence layer:** mini tracker (resting = next step + speaking tip) · "what ALICE logged" lines · voice states (listening / heard / thinking / ALICE) · option set rows · route card with auto-dismiss bar · timer.
- **Launcher:** brand block · cards with status chips · control rows with keycaps · collapsed controls summary · voice startup steps + progress · error card · START/STOP/RESTART states · update banner.

## Rejected directions (don't drift back unless asked)

- Colored dots / traffic lights for any status (fails grayscale) — includes the launcher's old status dots.
- Circle/diamond/triangle alert shapes or gauge needles for volatility (pulse glyphs won).
- Single-line horizontal filter bar; three-column side-by-side filter layout.
- Status colors as category colors (blue BUY / green SELL badges); green fills on step buttons like Mark Done.
- Option set as tall stacked cards — it's the search row, trimmed.
- A visible-but-disabled Finalize Run mid-run.
- "Terminal log" text-only style and chamfered "MobiGlas HUD" frames as the primary style (corner brackets survive as the one flourish).
- Rolled-up "route confidence" badge — the two per-terminal pulses are enough.
- Emoji as UI glyphs; filled icon sets.

## Workflow for a new ALICE UI request

1. Read `references/tokens.md` (always) and the relevant part of `references/components.md`. Check the actual widget code for which states exist.
2. Style exploration or new screen → one HTML page in `docs/wireframes/`, using the page scaffold from the asset files (dark backdrop, orange mono kicker, numbered state/option tags with name + trade-off, notes list). Send it with `SendUserFile`.
3. Reuse existing components verbatim where they apply.
4. Close with a recommendation and the one open decision, as a short question.
5. Implementing → read `references/qt-notes.md`; add tokens to `theme.py` rather than hard-coding hex values in widgets.
