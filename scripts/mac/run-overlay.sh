#!/bin/bash
# Launch the trade route filter/results overlay. Press OVERLAY_HOTKEY (see .env,
# default F3) to toggle it.
# Requires scripts/mac/setup.sh to have been run first.
#
# macOS-specific gotcha, first run only:
#   - Accessibility permission: the global toggle hotkey uses pynput, same as the
#     voice loop's PTT key — needs System Settings > Privacy & Security >
#     Accessibility (and possibly Input Monitoring on some macOS versions) granted to
#     whichever terminal app runs this script. Add it there if the hotkey doesn't
#     toggle the window.

cd "$(dirname "$0")/../.." || exit 1

if [ ! -x .venv/bin/python3 ]; then
    echo "No virtualenv found. Run scripts/mac/setup.sh first."
    exit 1
fi

if [ ! -f .env ]; then
    echo "No .env file found. Copy your API keys into a .env file in this folder first."
    exit 1
fi

echo "Starting Uplink overlay...  (Ctrl+C to stop)"
exec .venv/bin/uplink-overlay
