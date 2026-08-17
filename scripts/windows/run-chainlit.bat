@echo off
REM Launch the Chainlit chat UI in a browser tab.
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

.venv\Scripts\chainlit run app\main.py
pause
