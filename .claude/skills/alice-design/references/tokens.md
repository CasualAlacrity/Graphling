# ALICE Design Tokens

Derived from Argo Astronautics' orange-and-black industrial livery; not an official CIG brand kit. In code these are constants in `app/overlay/theme.py` (`theme.ACCENT`, `theme.SURFACE_ALT`, …) — add new tokens there, never hard-code hex in a widget.

## Color

### Surfaces
| Token | Hex | Use |
|---|---|---|
| `bg` | `#0D0F10` | Window background, launcher backdrop |
| `surface` | `#17191B` | Panels, results container, input fill, run cards |
| `surface-alt` | `#1F2224` | Cards inside panels, header rows, row hover, inactive tabs |
| presence surface | `rgba(23,25,27,.94)` | Presence-layer cards (slightly translucent over the game) |

### Structure
| Token | Hex | Use |
|---|---|---|
| `border` | `#33373A` | Input/panel borders, neutral card edge, inactive stepper |
| `border-subtle` | `#24272A` | Card borders, row dividers, rules |
| `grid` | `#1B1E20` | Launcher backdrop grid lines (60 × 52 px) |

### Text
| Token | Hex | Use |
|---|---|---|
| `text-primary` | `#EDEAE4` | Values, names, titles |
| `text-secondary` | `#9AA0A6` | Field labels, prices, eyebrows, ghost button text |
| `text-disabled` | `#5C6266` | Placeholders, units, codes, breadcrumbs, column headers, upcoming steps |

### Accent (Argo orange) — "do this now"
| Token | Hex | Use |
|---|---|---|
| `accent` | `#F2650C` | Primary button, focus border, active tab, "needs you now" card edge, route cards, current step, hover tick |
| `accent-hover` | `#FF7A1F` | Primary hover |
| `accent-active` | `#C94F00` | Pressed |
| `focus-ring` | `#FF9142` | Ghost-button hover border |
| `accent-muted` | `rgba(242,101,12,.16)` | Active toggle fill, update banner fill |
| `on-accent` | `#1A1200` | Text/icons on orange (and on green) fills |

Focus glow on inputs and key capture: `0 0 0 3px rgba(242,101,12,.18)` (CSS; see qt-notes for Qt).

> Code drift: `theme.py` currently uses `BG` for text on orange and `FOCUS_RING` for input focus. Target is `on-accent` and an `accent` border.

### Status — outcomes only, never decoration
| Token | Hex | Use |
|---|---|---|
| `success` | `#4CAF6D` | Stable pulse, done steps, finalized legs, positive profit, signed in, live, Finalize Run fill, ready-summary edge |
| `warning` | `#E8B23A` | Moderate pulse, "still needed" values (`???`), inline caution hints |
| `error` | `#E5484D` | Volatile pulse, losses, error cards, Stop-on-hover, abandon |
| `error-muted` | `rgba(229,72,77,.14)` | Error card / stop-hover fill |
| `info` | `#4A90D9` | Backdrop glow only. Not a UI category color. |

## Typography

| Role | Font | Weight / size |
|---|---|---|
| Launcher wordmark | Rajdhani | 700, 52px, letter-spacing 6px |
| Panel / state title | Rajdhani | 600, 17–20px |
| Card label, commodity/terminal name | Rajdhani | 600, 13–14px |
| Tabs, buttons | Rajdhani | 600, 13–14px |
| Big action (START ALICE) | Rajdhani | 700, 18px, letter-spacing 2px, uppercase |
| Values, prices, quantities | JetBrains Mono | 500–700, 11–15px (presence totals up to 20px) |
| Units (aUEC, SCU) | JetBrains Mono | 500, 10–11px, `text-disabled` |
| Codes, breadcrumbs, status chips | JetBrains Mono | 500, 10–10.5px |
| Column headers, eyebrows, kickers | JetBrains Mono | 600, 10–10.5px, uppercase, letter-spacing .08–.16em |
| Keycaps (hotkeys) | JetBrains Mono | 600, 11px |
| Field labels, helper text, sentences | System sans (`Segoe UI`, `Helvetica Neue`) | 500, 10.5–13px |

Fonts are bundled TTFs in `app/overlay/assets/fonts/` (both OFL), loaded by `theme.load_fonts()`. Mockups use Google Fonts `Rajdhani:wght@500;600;700` + `JetBrains+Mono:wght@400;500;600;700`.

## Shape and spacing

- Radii: panel / results container / run card **10px** · card **8px** · input, button, toggle **6px** · badge, chip, keycap **4px** · switches and round buttons fully round. Root workbench window and tabs' outer frame stay square (corner brackets sit there).
- Card padding 10–14px vertical, 14–16px horizontal. Workbench tab body padding 14px; launcher body 28px 30px.
- Input height 32px, padding 0 10px. Gap between fields 14px, between cards 12–16px.
- Card edge 3px. Row hover tick 3px, full height, left. Workbench corner brackets 14px × 2px.
- Workbench: fixed 60% of screen width × (screen height − 80px). Launcher: fixed 480px wide. Presence strip ~360px; option set ~780px.
- Shadow on floating windows: `0 20px 60px rgba(0,0,0,.45)`.

## Backdrops

- **Launcher / login pages / hero:** `bg`, a faint `grid` (60 × 52 px), and an orange radial glow from the top-right (`rgba(242,101,12,.20)` → 0). Painted in `LauncherWindow.paintEvent`.
- **Mockup pages only:** two soft radial glows (blue `rgba(74,144,217,.10)` top-left, orange `rgba(242,101,12,.08)` top-right) over `#08090A`; a "stage" with a few 1px star dots and a `border-subtle` frame.
