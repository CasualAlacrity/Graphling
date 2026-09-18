@echo off
REM Launch ALICE — the push-to-talk voice loop and the overlay, together in one process.
REM There is no voice-only or overlay-only mode; this is the way it runs.
REM
REM Hold PTT_HOTKEY (see .env) to speak. Press OVERLAY_HOTKEY (see .env, default F3) to
REM toggle the trade-run overlay.
REM
REM Requires scripts\windows\setup.bat to have been run first.
REM
REM No mic working yet? Set PTT_MODE=text in .env to type queries instead of speaking
REM them — everything downstream still runs the same way, it just skips recording.

cd /d "%~dp0..\.."

if not exist .venv\Scripts\python.exe (
    echo No virtualenv found. Run scripts\windows\setup.bat first.
    pause
    exit /b 1
)

if not exist .env (
    echo No .env file found. Copy your API keys into a .env file in this folder first.
    pause
    exit /b 1
)

if not exist .venv\Scripts\alice.exe (
    echo The 'alice' command is missing from the venv — this usually means the package
    echo was installed before it was renamed. Re-run scripts\windows\setup.bat, or:
    echo   .venv\Scripts\python -m pip install -e .
    pause
    exit /b 1
)

echo Starting ALICE...  (Ctrl+C to stop)
.venv\Scripts\alice
pause
