@echo off
REM One-time setup for ALICE on Windows.
REM Creates a Python 3.11 virtualenv and installs dependencies.
REM Just double-click this, or run `scripts\windows\setup.bat` from a terminal.

cd /d "%~dp0..\.."

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
REM everything from requirements.txt and registers the `alice` command used by the
REM run script below.
.venv\Scripts\python -m pip install -e .
if errorlevel 1 (
    echo.
    echo ERROR: dependency install failed. Scroll up for the offending package.
    pause
    exit /b 1
)

if not exist .env (
    echo Creating .env from the pilot settings ^(.env-pilot^)...
    copy .env-pilot .env >nul
) else (
    echo .env already exists, leaving it as it is.
)

REM No database here: the client talks only to ALICE's server, which owns Postgres. A
REM developer running a local ledger server follows the README instead.

echo.
echo Setup complete.
echo Run scripts\windows\run-alice.bat to start ALICE (voice + overlay together).
echo The first launch opens your browser to sign in with Discord.
pause
