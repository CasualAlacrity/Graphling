# ALICE Components

Live markup: `assets/alice-style-merge.html` (sections 1–3: workbench Route Search, In Progress, presence layer) and `assets/alice-launcher.html` (launcher, five states). Copy from those. "Code today" notes mark where the shipped widgets still differ from this spec.

## Contents
1. Workbench window and tabs
2. Filter panel
3. Volatility pulse
4. Results row
5. In Progress: run card, badges, stepper, finalize
6. Presence layer
7. Launcher
8. Shared icons and glyphs

---

## 1. Workbench window and tabs

- One frameless, always-on-top root window (`OverlayCanvas`, a `HudWindow`), 60% of screen width, centered, F3 toggles it. Square outer frame, `surface` fill, 1px `border`, orange 14px corner brackets painted at the four corners (root window only).
- Tabs: `Route Search`, `In Progress (n)`, `Ledger`. Rajdhani 600 14px; inactive = `surface-alt`, `text-secondary`, top corners 6px; active = `surface`, `accent` text and border. Count in mono `text-disabled`.
- No panel header inside a tab — the tab names it.

## 2. Filter panel

Three Cargo Manifest cards stacked, label column on the left (128px, Rajdhani 600 14px + 16px stroke icon), fields side by side on the right:

1. **Ship & Cargo** — neutral edge, cargo icon. Ship (search) · Cargo (number, `SCU` suffix inside the field) · Commodity (search).
2. **Source** — orange edge, pin icon. Terminal (search; breadcrumb as right-aligned suffix inside the field once resolved) · Min inventory (select).
3. **ROUTE connector** in the gap: 20px circle, `surface-alt` fill, 1px `accent` border, ↓ arrow in `accent`, then `ROUTE` in mono 10px `text-disabled` letter-spaced. Indented under the label column.
4. **Destination** — orange edge. Terminal · Max inventory.

Field label above each input (system sans 10.5px `text-secondary`, 4px gap). Inputs 32px, 6px radius, mono 12.5px; focus = `accent` border + glow. Invalid/unreachable terminal names keep today's validation tooltip behaviour.

**Footer row:** option toggles on the left, buttons on the right.
- **Option toggles are icon-only** 30×30 squares, 6px radius, 1px `border`, icon 15px `text-disabled`. On = `accent` border, `accent-muted` fill, icon `accent`. Label lives in the tooltip: "Space stations only", "Autoload-capable terminals only", "No-questions-asked terminals only (sells hot cargo)".
- **Reset** (ghost) + **Search** (primary).

Code today: flat section rows with a bottom rule and orange label text (no cards, no connector), inline field labels, breadcrumb only in the tooltip, square corners, filled icons, and a caption label next to the toggles.

## 3. Volatility pulse

`cv` = `volatility_price / price` (coefficient of variation) of that terminal's price history. A **confidence signal on the shown price**, not a judgment of the route. Thresholds come from sampling 210 live rows (p25 1.7%, median 3.6%, p75 7.0%, p90 11.2%) — `VOLATILITY_STABLE_MAX` / `VOLATILITY_MODERATE_MAX` in `results_panel.py`.

| Tier | CV | Shape | Color |
|---|---|---|---|
| Stable | < 3% | flat line, tiny ripple | `success` |
| Moderate | 3–8% | one smooth wave | `warning` |
| Volatile | > 8% | sharp jagged spikes | `error` |
| No history | unknown | dashed flat line | `text-disabled` |

Silhouettes must stay distinct in grayscale. Render ~22–24 × 13–14px, stroke 1.6–1.8, round caps/joins, no fill. Directly left of the terminal name wherever a terminal price shows. Tooltip always gives the exact value: `Moderate volatility (4.2%)`.

Mockup SVGs (viewBox `0 0 26 14`):
```html
<path d="M0 7 L8 7 L11 6.3 L14 7.5 L26 7"/>                               <!-- stable -->
<path d="M0 7 Q6.5 1 13 7 T26 7"/>                                        <!-- moderate -->
<path d="M0 7 L3 7 L5 1 L7 13 L9 3 L11 11 L13 5 L15 9 L17 6 L26 7"/>     <!-- volatile -->
<path d="M1 7 L25 7" stroke-dasharray="2.5 3.5"/>                         <!-- no history -->
```
In Qt it's `VolatilityIcon.paintEvent` (QPainterPath from fixed per-tier points). Code today draws "no history" as a plain grey solid line — make it dashed.

## 4. Results row

Container: `surface`, 1px `border`, 10px radius. Header row `surface-alt`, mono 600 10.5px uppercase `text-disabled`: `COMMODITY · LOAD · ORIGIN · BUY/SCU · TRAVEL · DESTINATION · SELL/SCU · [Profit ◯ Margin switch]`.

Columns (CSS reference): `minmax(150px,1.2fr) 70px minmax(200px,1.5fr) 74px minmax(200px,1.5fr) minmax(140px,1fr) 30px`, gap 14px, row padding 11px 18px. In Qt the header and rows share fixed widths for the non-stretching columns (`SCU_COLUMN_WIDTH`, etc.).

- **Commodity:** cargo icon 17px `text-secondary` + name (Rajdhani 600 14px, ellipsis) over code (`LARA`, mono 10.5px `text-disabled`).
- **Load:** `576 SCU`, right-aligned, mono 13px **`text-secondary`** — deliberately quieter than profit.
- **Origin / Destination:** pulse + three lines: terminal name (Rajdhani 600 13.5px) · breadcrumb (mono 10px) · `buy 2,735 aUEC` / `sell 3,120 aUEC` (mono 11px `text-secondary`, `buy`/`sell` and unit `text-disabled`).
- **Travel:** → arrow 18px `text-disabled` with the travel time under it (mono 10px `text-secondary`). Rough estimates show `~14m` in `text-disabled`; tooltip explains why.
- **Profit / Margin:** big = the sorted metric (`221,760 aUEC`, mono 700 15px), small = the other (`14.1%`, mono 11px `text-secondary`). The switch flips which is primary.
- **Add:** 28px circle, 1px `border`, `+` stroke icon. Hover: `accent` fill, `on-accent` icon. Tooltip "Add to Active Routes".
- **Row:** divider `border-subtle`; hover = `surface-alt` + 3px `accent` tick on the left edge. Vertical scroll allowed; horizontal never.

Code today: `📦 CODE` emoji label with the name only in a tooltip, no breadcrumb line (name label holds the combined breadcrumb), separate travel-time column, SCU at `text-primary`.

## 5. In Progress: run card, badges, stepper, finalize

**Run card:** `surface`, 10px radius, 14px padding. Top line: ship · commodity (Rajdhani 600 14px, 2px `accent` left rule) · age (mono 10px `text-disabled`) · right-aligned money pairs (eyebrow `LOAD` / `INVESTED` mono 9.5px over mono 700 12.5px values).

**Leg row:** chevron ▸/▾ · badge · terminal name (Rajdhani 600 13px) over detail (`576 SCU @ 2,735 aUEC`, mono 10.5px) · status chip on the right.
- **BUY/SELL badge:** Rajdhani 700 10.5px, 1px `border`, 4px radius, `text-secondary`, with a ↓ (buy) / ↑ (sell) arrow. Neutral — not status colors.
- **Status chip:** mono 10px, 4px radius: `✓ finalized` (`success`), current milestone in words (`unloading`, `accent`), pending (`text-disabled`).

**Stepper** (expanded current leg): nodes 22px circles on a 2px rail, labels Rajdhani 600 10.5px. Done = `success` fill with ✓ and green rail; current = `accent` fill with number and orange label; upcoming = `border` outline. Travel is node 1; the rest come from `trade_run_store.breadcrumb_steps` (Buy cargo / Confirm loaded / Finalize; Confirm unloaded / Sell cargo / Finalize). The step action under it is **Mark Done — orange primary, full width**.

**Finalize, two states (rule: it doesn't exist until it can be used):**
- *Not ready* (any leg unfinalized): dim "upcoming" row — dashed 14px ring, "Run summary and finalization" (Rajdhani 600 italic `text-disabled`), right-aligned `after both legs are finalized` (mono 10px). **No button.**
- *Ready* (all legs finalized): a summary card with a **green** left edge — Revenue / Invested / fees lines (mono 11.5px), divider, **Profit** (mono 700 15px, green or red, always signed), a one-line lock warning ("Moves this run to the Ledger. Recorded values can't be changed after this."), then **Finalize Run** — `success` fill, `on-accent` text, full width. The only green button in the app.

Code today: Mark Done/Finalize/Confirm are green fills, BUY/SELL are blue/green colored badges, and a disabled Finalize Run sits beside the upcoming row before it's ready.

**Ledger:** day groups (Rajdhani title with 2px orange rule), run rows (commodity · route · SCU in/out · signed profit green/red), day total row on `surface-alt`. Same type rules; no new colors.

## 6. Presence layer

All cards: presence surface (`rgba(23,25,27,.94)`), 1px `border`, 8px radius. Non-modal, never takes focus, fade out on their own, never scroll. Orange left/top edge marks ALICE's current focus.

- **Mini tracker, resting** (~360px): `NEXT` (mono 10px `accent` letter-spaced) · step (Rajdhani 600 17px, e.g. "Travel to Area18 TDD") · speaking tip in system sans 11.5px: *When you land, just say it, e.g. "I'm at Area18."* — rotate example phrasings; teach "say it naturally", not a command.
- **What ALICE logged** (transient, fades after ~4s back to resting): transition lines `✓ Cargo sold` (green check glyph, Rajdhani 600 14px); indented value lines in mono 11.5px — `ⓘ 3,120 aUEC/SCU` (circle-i, `text-secondary`) and `⚠ Autoload fees: ???` (triangle-!, `warning`, `???` in `warning`). Never grows into a session log.
- **Voice states** (same strip): mic disc 26px + state word (mono 10px letter-spaced) + content. LISTENING = orange mic with glow + level bars; HEARD = the transcript in italic `text-primary`; THINKING = three fading dots; ALICE = caption of the reply.
- **Option set** = the results row, trimmed (~780px): columns `# · Commodity · From · Travel · To · Profit / hour`. `#` is a 24px square number (the click target; voice: "go with option 2"). SCU moves under the code (`LARA · 576 SCU`, dimmed). Profit/h is the big right-hand number — receipt order left-to-right, total last, matching how ALICE says it. Option 1 (ALICE's lead) gets the 3px orange tick and an orange number; the runner-up shows its delta (`−18,400 /h vs 1`). Nothing is labelled "best". Max 2–3 rows.
- **Route card** (workbench closed): `SUGGESTED ROUTE` eyebrow, title `576 SCU Laranite → Area18 TDD`, mono detail line, Dismiss (ghost) + Accept (primary), and a 2px orange auto-dismiss countdown bar along the bottom. Never accepts on its own.
- **Timer:** ring (orange arc on `border-subtle` track) + mono 700 20px `03:12` + label.

## 7. Launcher

Fixed 480px window, normal OS title bar, launcher backdrop (grid + orange glow). Closing quits ALICE.

- **Brand block:** `ALICE` wordmark, 52 × 4px orange bar, acronym tagline (system sans 600 10.5px, initials in `accent`).
- **Cards** (Pilot / Controls / Voice / error): Cargo Manifest card with an eyebrow (mono 10.5px letter-spaced `text-secondary`) and a **status chip** on the right: glyph + word, 4px radius — `→ sign in` (`accent`), `✓ signed in` (`success`), `● live` (`success`, dot with halo), `◌ starting` (`accent`, spinner ring), `✕ stopped` (`error`), neutral chips like `defaults` / `locked while running`. Orange edge on the one card that needs the pilot now.
- **Pilot:** avatar circle (Discord avatar, initial as fallback) + username (Rajdhani 600 21px) + Sign in with Discord (primary) or Switch account (ghost).
- **Controls rows:** 104px label column (system sans 12.5px `text-secondary`) + 32px inputs. Hotkeys render as **keycaps** (mono 600 11px, `surface-alt`, 1px `border` with 2px bottom, 4px radius) with an optional hint after — device name in `text-disabled`, or a caution in `warning` ("also types capitals" for Right Shift). Key capture = input in `accent` border + glow, text "Press a key or button…". PTT sounds = a switch, not a checkbox.
- **While running** Controls collapse to one summary line of keycaps and device names, with "Stop ALICE to change these." — they're disabled then anyway.
- **Voice card:** startup as a step list — `✓ Connected to ALICE's server`, `✓ Overlay ready`, `◌ Downloading the speech model (first launch only)`, `◌ Listening` (dashed, upcoming). 4px orange progress bar with mono meta (`178 / 466 MB` · "ALICE can't hear you until this finishes"). Live: "Hold [Button 12] to talk to ALICE", with keycaps.
- **Error card:** red edge, faint red fill, triangle glyph + eyebrow in `error` naming what failed, then plain cause + what to do. The failed step shows ✕ in the step list. The relevant control reappears editable.
- **Big button:** START ALICE (primary) · disabled with a reason line ("Sign in first") · STOP ALICE outlined `text-secondary` while starting, outlined `success` while live, `error` outline + `error-muted` fill on hover · RESTART ALICE (primary) after a failure. Open logs (ghost) beside it.
- **Update banner:** `accent-muted` fill, `accent` border, 8px radius, versions in mono, Download (primary). Notify-only; ALICE never installs.
- **Footer:** last log line (mono 10.5px `text-disabled`, elided) + version.

Code today: square cards with an 8px colored status dot (color-only), checkbox for PTT sounds, controls disabled but full-height while running, a single changing sentence for voice progress.

## 8. Shared icons and glyphs

Stroke-only, 24px viewBox, stroke 1.6 (2–2.8 for small glyphs), round caps/joins, `currentColor`. In Qt, ship as SVG files in `app/overlay/assets/icons/` and tint via `theme.load_icon(name, color)`.

```html
<!-- cargo box --> <path d="M3 8l9-4 9 4-9 4-9-4z"/><path d="M3 8v9l9 4 9-4V8"/><path d="M12 12v9"/>
<!-- pin -->       <path d="M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11z"/><circle cx="12" cy="10" r="2"/>
<!-- arrow → -->   <path d="M4 12h16M14 6l6 6-6 6"/>
<!-- down / up --> <path d="M12 4v15M6 13l6 6 6-6"/>  /  <path d="M12 20V5M6 11l6-6 6 6"/>
<!-- plus -->      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
<!-- station -->   <circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6M3 12h6M15 12h6"/>
<!-- loader -->    <path d="M4 5v11h14"/><circle cx="8" cy="19" r="2"/><circle cx="16" cy="19" r="2"/><rect x="8" y="8" width="8" height="6" rx="1"/>
<!-- mask (NQA) --><path d="M3 9c0-2 2-3 9-3s9 1 9 3c0 5-3 8-6 8-1.5 0-2-2-3-2s-1.5 2-3 2c-3 0-6-3-6-8z"/><circle cx="8" cy="11" r="1.5"/><circle cx="16" cy="11" r="1.5"/>
<!-- check -->     <path d="M5 12.5l4.5 4.5L19 7.5"/>
<!-- info -->      <circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.5" r=".6" fill="currentColor"/>
<!-- needed / warning --> <path d="M12 3l9.5 17h-19z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.3" r=".6" fill="currentColor"/>
<!-- x -->         <path d="M6 6l12 12M18 6L6 18"/>
<!-- mic -->       <rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>
<!-- rocket -->    <path d="M12 2c2.5 2 4 5.5 4 9.5 0 2-1 4-1 4l-3 2-3-2s-1-2-1-4c0-4 1.5-7.5 4-9.5z"/><path d="M9 15l-3 1 1-3M15 15l3 1-1-3"/><circle cx="12" cy="10" r="1.4"/>
```
Code today: `plane-up`, `cart-flatbed`, `trash-can` are filled self-authored icons and `mask` is Font Awesome (filled, attribution in the SVG). Replace with the stroke versions above when those widgets are touched.
