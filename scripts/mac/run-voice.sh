#!/bin/bash
# Launch the push-to-talk voice loop. Hold PTT_HOTKEY (see .env) to speak.
# Requires scripts/mac/setup.sh to have been run first.
#
# macOS-specific gotchas, first run only:
#   - Microphone permission: the terminal app running this (Terminal/iTerm/etc.) needs
#     mic access. macOS should prompt automatically the first time; if it doesn't, add
#     it under System Settings > Privacy & Security > Microphone.
#   - Accessibility permission: pynput's global hotkey listener (PTT_HOTKEY) needs it,
#     same place: System Settings > Privacy & Security > Accessibility. Add your
#     terminal app and enable it. If PTT still doesn't respond after that, also check
#     Privacy & Security > Input Monitoring — some macOS versions gate key listeners
#     there instead of (or in addition to) Accessibility.
#   - No mic/hotkey working yet? Set PTT_MODE=text in .env to type queries instead of
#     speaking them — everything downstream (Whisper, the graph, ElevenLabs) still runs
#     the same way, it just skips the recording step.

cd "$(dirname "$0")/../.." || exit 1

if [ ! -x .venv/bin/python3 ]; then
    echo "No virtualenv found. Run scripts/mac/setup.sh first."
    exit 1
fi

if [ ! -f .env ]; then
    echo "No .env file found. Copy your API keys into a .env file in this folder first."
    exit 1
fi

echo "Starting Uplink voice loop...  (Ctrl+C to stop)"
exec .venv/bin/uplink-voice
