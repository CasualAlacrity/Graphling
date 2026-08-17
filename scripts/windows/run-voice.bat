@echo off
REM Launch the push-to-talk voice loop. Hold PTT_HOTKEY (see .env) to speak.
REM Requires win-setup.bat to have been run first.

cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo No virtualenv found. Run win-setup.bat first.
    pause
    exit /b 1
)

if not exist .env (
    echo No .env file found. Copy your API keys into a .env file in this folder first.
    pause
    exit /b 1
)

echo Starting Uplink voice loop...  (Ctrl+C to stop)
.venv\Scripts\uplink-voice
pause
