# PySide6 / QSS Implementation Notes

Mockups are HTML/CSS; ALICE is PySide6. This is what translates, what needs a workaround, and how the existing code is organised.

## Where things live

- `app/overlay/theme.py` — tokens (`theme.ACCENT`, …), font stacks (`DISPLAY_FONT`, `MONO_FONT`, `LABEL_FONT`), the shared `STYLESHEET`, `load_fonts()`, `load_icon(name, color)`, and `HudWindow` (corner brackets).
- `app/launcher/launcher_window.py` — `LAUNCHER_STYLESHEET` appended to `theme.STYLESHEET`, `_Card`, and the painted grid/glow backdrop.
- Widgets are styled by **`objectName`** selectors (`QLabel#routeProfit`) with **dynamic properties** for variants/states (`QLabel#legStatus[status="success"]`, `QPushButton#startButton[running="true"]`). Keep that convention. After changing a property at runtime, re-polish (`w.style().unpolish(w); w.style().polish(w)`) or rebuild the widget — the panels mostly rebuild rows on refresh.

## Translates directly to QSS

- Colors, 1px borders, per-side borders (`border-left: 3px solid #F2650C` for card edges), `border-radius` (cards 8px, inputs/buttons 6px, chips 4px), padding, `font-family/size/weight`.
- `:hover`, `:pressed`, `:focus`, `:checked`, `:disabled`.
- `rgba()` fills (`ACCENT_MUTED`, `ERROR_MUTED`).
- **Uppercase and letter-spacing:** QSS has no `text-transform`, so write eyebrows and headers pre-uppercased. For letter-spacing, `theme.py` says QSS lacks it while `LAUNCHER_STYLESHEET` uses it anyway. Check it on screen. `QFont.setLetterSpacing` is the reliable route.

## Needs a workaround

- **Shadows / focus glow:** no `box-shadow`. Floating windows: `QGraphicsDropShadowEffect` (blur ~40, y-offset ~20, rgba(0,0,0,115)). Input focus glow: approximate with an `accent` border; a tinted drop-shadow effect is possible but costly on many inputs — don't.
- **Row hover tick / orange "lead" tick:** QSS can't add pseudo-elements. Use a `border-left: 3px solid` on the row frame for the state, or paint it in a delegate (`QStyledItemDelegate.paint`, `State_MouseOver`).
- **Pulse glyphs, stepper nodes, switches, progress rings:** painted (`QPainter`/`QPainterPath`) — see `VolatilityIcon`, `SortToggle`, `_TravelNode`. Dashed "no history" line: `QPen.setDashPattern([2.5, 3.5])`.
- **Icons:** SVGs in `app/overlay/assets/icons/` using `currentColor`; `theme.load_icon(name, color)` bakes the tint (QSS `color` doesn't tint icons). Re-render the icon on state change (e.g. toggle on → `accent`).
- **Tooltips for icon-only toggles:** `setToolTip(...)`; style `QToolTip` with `surface-alt` background, `border`, `text-primary`.
- **Keycaps inside an input:** a small `QLabel#keycap` (border + 2px bottom border + 4px radius) laid out inside the button/frame, not rich text.
- **Breadcrumb suffix inside a terminal input:** a `QLabel` positioned at the right inside the `QLineEdit` (child widget + `setTextMargins` on the right so typing doesn't run under it), shown only once the name resolves.
- **Emoji:** don't — Qt on Windows renders them inconsistently. Use the stroke glyphs (check / info / triangle / x).
- **Presence-layer windows:** frameless + always-on-top + must not steal focus: `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus`, plus `WA_ShowWithoutActivating`; translucent card fill needs `WA_TranslucentBackground` and painting the rounded rect yourself. Click-through is deliberately *not* set (decision in `docs/presence-layer.md`). Fade with `QPropertyAnimation` on `windowOpacity`.
- **Chamfered corners / clip-path:** not supported without a custom `paintEvent` — one reason the HUD style was rejected.

## Scrolling

- Every scroll area: `setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)` — never trust "as needed". Fixed-width columns must sum to less than the narrowest panel width and truncate (`elidedText`) rather than overflow.
- Vertical scrollbars only on the results list, In Progress, and Ledger (thin, `border` handle, `accent` on hover — already in `theme.STYLESHEET`). None in the launcher or presence layer.

## Fonts and numerals

- `theme.load_fonts()` must run after `QApplication()` and before any styled widget.
- JetBrains Mono has tabular figures; right-align numeric columns with `Qt.AlignRight | Qt.AlignVCenter`.
- Header row + item rows only line up if both use the same fixed widths for the non-stretching columns (see `SCU_COLUMN_WIDTH` etc. in `results_panel.py`).
