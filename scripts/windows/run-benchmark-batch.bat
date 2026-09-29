@echo off
REM Runs evals/agent_eval/run_batch.py — pulls whatever Ollama models the CONFIGS
REM list in that file needs, runs the agent_eval harness once per config, then
REM commits and pushes the resulting report files back to origin.
REM
REM Meant for a second benchmark machine (e.g. a 5090 PC) that isn't where the main
REM dev/git history lives — after this finishes, `git pull` on your usual machine
REM to see the results.
REM
REM Edit evals\agent_eval\run_batch.py's CONFIGS list to choose what a given batch
REM covers. Requires scripts\windows\setup.bat to have been run first, a working
REM .env, and Ollama installed and on PATH.

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

where ollama >nul 2>nul
if errorlevel 1 (
    echo Ollama isn't on PATH. Install it from https://ollama.com and try again.
    pause
    exit /b 1
)

echo Running the agent_eval benchmark batch...
.venv\Scripts\python evals\agent_eval\run_batch.py
if errorlevel 1 (
    echo.
    echo Batch finished with errors — see the output above.
    pause
    exit /b 1
)

echo.
echo Done. Report files pushed — run `git pull` on your usual machine to see them.
pause
