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
REM Editable install of the project itself (pyproject.toml), which also pulls in
REM everything from requirements.txt and registers the uplink-overlay/uplink-voice
REM commands used by the run scripts below.
.venv\Scripts\python -m pip install -e .
if errorlevel 1 (
    echo.
    echo ERROR: dependency install failed. Scroll up for the offending package.
    pause
    exit /b 1
)

where docker >nul 2>nul
if errorlevel 1 (
    echo.
    echo WARNING: Docker not found on PATH. Install Docker Desktop, then run
    echo   docker compose up -d
    echo yourself before using the trade route tracker or overlay.
) else (
    echo Starting Postgres ^(trade route tracker storage^)...
    docker compose up -d --wait
    if errorlevel 1 (
        echo.
        echo WARNING: docker compose up failed. Is Docker Desktop running?
    ) else (
        echo Applying database migrations...
        .venv\Scripts\python -m alembic upgrade head
        if errorlevel 1 (
            echo.
            echo WARNING: alembic upgrade failed. Scroll up for the error.
        )
    )
)

echo.
echo Setup complete.
echo Make sure a .env file with your API keys exists in this folder before running.
echo Run win-run-voice.bat for push-to-talk, or win-run-chainlit.bat for the chat UI.
pause
