# Client UI — capabilities and affordances (for wireframing)

**Status:** written 2026-10-12 as the brief for Jeff's wireframes. Nothing here is built
unless it says so.

**Scope:** what the shipped Windows client needs on screen so a pilot can install ALICE, sign in
with Discord, and use her without ever touching a `.env` file. The two in-game surfaces already
have their own docs and are only summarised here:
- **The workbench** (built): the F3 window with Route Search, In Progress and Ledger tabs.
- **The presence layer** (designed, not built): `docs/presence-layer.md`.

**Priority:**
- **P0:** needed before the first outside pilot.
- **P1:** soon after, once a few pilots are using it.
- **P2:** later.

## Ground rules from earlier decisions

These bind every screen below:

- **No scrollbars in the overlay.** A scrollbar means the layout failed. Curate, cap, or
  collapse instead.
- **Information, not decisions.** ALICE shows information and options; the pilot chooses. No
  auto-play, and no screen that commits something on the pilot's behalf.
- **Glanceable mid-flight.** Anything shown during play is read in a second, is non-modal,
  never steals focus from the game, and fades on its own.
- **The workbench and the presence layer stay separate windows** with different lifecycles.
  F3 toggles the workbench for planning; the presence layer is the small in-game strip.
- **Infrastructure problems are never voiced by ALICE.** She doesn't apologise for servers.
  Status shows up as UI here and, for outages, in a public Discord status channel
  (`docs/intent/observability.md`).
- **Windows first.** Star Citizen pilots are on Windows. The overlay sits over the game, which
  must run in borderless windowed mode.

## 1. Install and first launch

| Capability | Affordances / states | Pri | Notes |
|---|---|---|---|
| Install | Installer with progress, done, launch ALICE | P0 | Packaging is its own plan (a Windows installer test first). |
| First-launch download | Progress for the speech-recognition model (Whisper `base`, ~140 MB): "Getting ALICE's ears ready" | P0 | Downloads on first use today, silently. A pilot would see a frozen app. |
| Microphone access | Windows mic permission explained if it's blocked; retry | P0 | |
| Server unreachable at launch | "Can't reach ALICE's server", with retry and a status-channel link | P0 | |

## 2. Sign in (Discord)

How it works today (`auth/discord_identity.py`): the browser opens the server's Discord login,
and a local listener on port 8765 catches the result. Built; it has no UI yet.

| Capability | Affordances / states | Pri | Notes |
|---|---|---|---|
| Sign in | "Sign in with Discord" button | P0 | Today the browser simply opens at startup. |
| Waiting on the browser | "Finish signing in in your browser…" with cancel and open-again | P0 | The wait is unbounded today. |
| Signed in | Discord name (and avatar, P1) shown somewhere persistent | P0 | |
| Failed or cancelled | Reason, plus try again | P0 | |
| Session renewed | Pre-emptive renewal at startup when under 24 h is left: the same waiting state, with copy explaining why | P0 | Built 2026-10-12 (logic only). |
| Mid-session re-login | Notice: "Your login expired — finish signing in in your browser"; ALICE waits | P0 | Built (logic). Today it's a console print only. |
| Not in the beta | "This Discord account isn't in the beta yet", with a link to request access | P1 | Discord role gating isn't built (docs/llm-gateway.md). |
| Sign out | In settings | P1 | |
| Port 8765 in use | Detect it and pick another port automatically, or explain | P1 | Today it's `DISCORD_LOCAL_REDIRECT_URI` in `.env`. |

## 3. First-run setup (wizard, once)

Every item here replaces something a developer types into `.env` today.

| Capability | Affordances / states | Pri | Notes |
|---|---|---|---|
| Privacy notice | Plain words: what goes through ALICE's server and the home box; we log usage, never conversation content; debug logging is opt-in. "Got it" to continue | P0 | Required by docs/llm-gateway.md before outside users. |
| Push-to-talk key | "Press the key you want to hold to talk"; shows the bound key; warns about keys you also type with (right Shift is the trap) and keys Star Citizen already binds | P0 | `PTT_HOTKEY` (default `shift_r`). |
| Workbench hotkey | Same picker | P0 | `OVERLAY_HOTKEY` (default F3). |
| Microphone | Device list, live level meter, "Say something" test that shows **what ALICE heard** (the Whisper transcript) | P0 | Uses the system default device today; there's no picker. |
| Speaker / voice test | Output device, volume, "Hear ALICE" plays a sample line | P0 | |
| Text-mode fallback | "Type instead of talk" toggle | P1 | `PTT_MODE=text`. |
| First-use teaching | The three-beat loop from presence-layer.md: say it naturally; example phrases rotate | P1 | Belongs with the mini tracker. |

## 4. The voice loop: what the pilot sees while talking

Today the loop is `listen → think → speak`, strictly one after another, with no visible state
outside the console.

| Capability | Affordances / states | Pri | Notes |
|---|---|---|---|
| Listening | Indicator while push-to-talk is held | P0 | |
| Heard | **The transcript of what ALICE heard**, shown briefly | P0 | Biggest trust win: the pilot sees a mishearing before acting on the answer. |
| Thinking | Indicator from the moment speech ends until the answer starts | P0 | The `request_received` intent (presence-layer.md). Avoids silence that looks like "she didn't hear me". |
| Speaking | Indicator, plus **captions** of the reply | P0 | Captions are also the fallback when speech is unavailable (below). |
| No voice this turn | Captions only, with a small "voice unavailable" mark | P0 | Built in the logic: `/tts` 503 or limit gives empty audio. |
| Turn failed | Short, non-ALICE system message (server down, rate limited) | P0 | 429 / 503 from the gateways. |
| Stop talking / interrupt | Barge-in: press push-to-talk to cut ALICE off | P2 | Needs the voice-loop rework. |
| Volume / mute ALICE | Quick control | P1 | |
| Pause listening | Global toggle (also in the tray) | P1 | |

## 5. Presence layer (designed — see `docs/presence-layer.md`)

**Content (P0–P1):**
- the mini tracker: next step plus a speaking tip, then ✔️/ℹ️/❗ "what I logged" lines that
  fade back to just the next step;
- option cards for comparing two routes, read in the same order ALICE says them;
- timer;
- route card, with Accept / Dismiss, non-modal and auto-dismissing.

**The wireframe also needs to decide:**
- where the strip sits (corner, which monitor);
- how it coexists with the voice-loop indicators in section 4. They're probably the same strip.

## 6. Workbench (built; F3)

- **Route Search** (filter + results), **In Progress** (trade runs), **Ledger** (finalised runs).
- No new capabilities needed for launch.
- Worth checking in the wireframe pass: the signed-in identity, and a status line
  (section 8), since this is the one place a pilot looks at calmly.

## 7. Settings (after setup; reachable from the tray and the workbench)

| Section | Contents | Pri |
|---|---|---|
| Account | Discord name, sign out | P1 |
| Controls | Push-to-talk key, workbench hotkey, type-instead-of-talk | P0 (same pickers as setup) |
| Audio | Microphone + level + test, output + volume + test voice | P0 |
| Overlay | Strip position, monitor, size or opacity | P1 |
| Privacy & data | What's collected (same text as setup); opt-in debug logging / full tracing for a bug report. The local transcript log (`transcript_log.py`, built 2026-10-12): where it is, open the folder, clear it, turn it off | P1 |
| About | Version, update status, links (Discord, status channel, website) | P0 (version), P1 (rest) |

**Developer-only knobs that stay out of the UI** (baked in or `.env` for development):
`ALICE_API_URL`, `LLM_PROVIDER`/`TTS_PROVIDER` (always `alice` when shipped), `MAX_LIVE_ROUTE_FETCHES`,
`HISTORY_*`, `PREWARM_OLLAMA`, `ANTHROPIC_CACHE_*`, `LANGSMITH_*` (off for pilots).

## 8. Status and health (always reachable, never loud)

| Capability | Affordances / states | Pri | Notes |
|---|---|---|---|
| Connection | Connected / reconnecting / server unreachable | P0 | Tray icon state, plus the workbench status line. |
| Degraded voice or brain | "Running on backup" when frankencompute is down and Haiku or ElevenLabs answers | P1 | Open question in docs/llm-gateway.md: show it at all? Lean: a small indicator, never spoken. |
| Limits hit | "Slow down a moment" (rate limit); "Voice resumes tomorrow" (daily speech guard) | P0 | Text from the 429 responses. |
| Data freshness | Prices are from UEX, cached up to 30 min | P2 | Relevant once crowd-sourced OCR data exists. |

## 9. App lifecycle (Windows tray)

| Capability | Affordances / states | Pri |
|---|---|---|
| Tray icon | Open workbench, pause listening, settings, status, quit | P0 |
| Start with Windows | Toggle | P2 |
| Updates | "Update available", then update now or later; required updates block with an explanation | P1 (the mechanism comes with packaging) |
| Report a problem | Opens a form or Discord link; can attach logs and turn on full tracing for this session | P1 |

## 10. Help

| Capability | Affordances | Pri |
|---|---|---|
| What can I ask? | Example phrases by activity (trading, mining, salvage), rotating | P1 |
| Hotkey cheat sheet | Push-to-talk and workbench keys | P0 (one line on the strip or in the tray tooltip) |

## Questions for the wireframe pass

1. **One in-game strip or two?** The voice-loop state (section 4) and the presence layer (section
   5) are probably one strip with modes, not two windows.
2. **Is the workbench also the settings home**, as a fourth tab, or is settings its own window
   from the tray?
3. **Captions:** always on, or only when voice fails? Always-on helps with noisy game audio but
   adds screen weight.
4. **How much of "running on backup" to show:** nothing, an icon, or a line.
5. **First-run order:** sign in first (so settings sync later), or setup first (so the pilot
   hears ALICE before creating anything)?
