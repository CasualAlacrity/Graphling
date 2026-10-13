@echo off
REM Build ALICE-Setup-<version>.exe, the installer beta pilots download.
REM
REM Needs, once:
REM   - scripts\windows\setup.bat already run (the .venv this builds from)
REM   - Inno Setup 6, free: https://jrsoftware.org/isdl.php (default install location)
REM
REM Output: packaging\dist\ALICE-Setup-<version>.exe
REM (packaging\dist\ALICE\ALICE.exe is the unpacked app, handy for a quick test run.)

cd /d "%~dp0..\.."

if not exist .venv\Scripts\python.exe (
    echo No virtualenv found. Run scripts\windows\setup.bat first.
    pause
    exit /b 1
)

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php
    pause
    exit /b 1
)

echo Installing build tools...
.venv\Scripts\python -m pip install --quiet "pyinstaller>=6.10"
if errorlevel 1 goto failed

REM The version comes from pyproject.toml, so there's one place to bump it.
for /f %%v in ('.venv\Scripts\python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"') do set APP_VERSION=%%v
echo Building ALICE %APP_VERSION%...

.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath packaging\dist --workpath packaging\build packaging\alice.spec
if errorlevel 1 goto failed

%ISCC% /Qp /DAppVersion=%APP_VERSION% packaging\alice.iss
if errorlevel 1 goto failed

echo.
echo Done: packaging\dist\ALICE-Setup-%APP_VERSION%.exe
pause
exit /b 0

:failed
echo.
echo ERROR: the build failed. Scroll up for the first error.
pause
exit /b 1
