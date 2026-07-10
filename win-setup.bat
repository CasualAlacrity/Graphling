@echo off
REM One-time setup for Graphling/Uplink on Windows.
REM Creates a Python 3.11 virtualenv and installs dependencies.
REM Just double-click this, or run `win-setup.bat` from a terminal.

cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Creating virtualenv with Python 3.11...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: could not create the venv with Python 3.11.
        echo Check that Python 3.11 is installed:  py -3.11 --version
        pause
        exit /b 1
    )
) else (
    echo Virtualenv already exists, reusing it.
)

echo Installing dependencies ^(this can take a few minutes the first time^)...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: dependency install failed. Scroll up for the offending package.
    pause
    exit /b 1
)

echo.
echo Setup complete.
echo Make sure a .env file with your API keys exists in this folder before running.
echo Run win-run-voice.bat for push-to-talk, or win-run-chainlit.bat for the chat UI.
pause
