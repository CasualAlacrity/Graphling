@echo off
REM Upload the installer to the Hetzner box so pilots can download it from
REM   https://heyalice.help/download/ALICE-Setup.exe          (always the newest)
REM   https://heyalice.help/download/ALICE-Setup-<version>.exe (that exact build)
REM
REM Run scripts\windows\build-installer.bat first. Needs, once:
REM   - the Hetzner SSH key on this PC at SSH_KEY below (the same key
REM     scripts/deploy_server.sh uses on the Mac)
REM   - the one-time nginx setup in docs/deploy.md, "Installer downloads"
REM
REM Uses Windows' built-in OpenSSH (ssh/scp), nothing to install.

cd /d "%~dp0..\.."

REM -- Edit these once --------------------------------------------------------
set PROD_HOST=168.119.234.226
set PROD_USER=root
set SSH_KEY=%USERPROFILE%\.ssh\flockt
set DOWNLOAD_DIR=/var/www/alice-downloads
REM ---------------------------------------------------------------------------

if not exist "%SSH_KEY%" (
    echo SSH key not found at %SSH_KEY%
    echo Copy the Hetzner key there, or edit SSH_KEY at the top of this script.
    pause
    exit /b 1
)

for /f %%v in ('.venv\Scripts\python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"') do set APP_VERSION=%%v
set INSTALLER=packaging\dist\ALICE-Setup-%APP_VERSION%.exe

if not exist "%INSTALLER%" (
    echo %INSTALLER% not found. Run scripts\windows\build-installer.bat first.
    pause
    exit /b 1
)

echo Uploading ALICE %APP_VERSION% to %PROD_HOST%...
REM Uploaded under a temporary name and renamed once complete, so nobody downloads a
REM half-uploaded installer. ALICE-Setup.exe is then replaced in one step (cp to a temp
REM name, then mv), which is the stable link to share.
scp -i "%SSH_KEY%" "%INSTALLER%" %PROD_USER%@%PROD_HOST%:%DOWNLOAD_DIR%/ALICE-Setup-%APP_VERSION%.exe.part
if errorlevel 1 goto failed

ssh -i "%SSH_KEY%" %PROD_USER%@%PROD_HOST% "cd %DOWNLOAD_DIR% && mv ALICE-Setup-%APP_VERSION%.exe.part ALICE-Setup-%APP_VERSION%.exe && cp ALICE-Setup-%APP_VERSION%.exe .ALICE-Setup.exe.tmp && mv .ALICE-Setup.exe.tmp ALICE-Setup.exe && ls -lh"
if errorlevel 1 goto failed

echo.
echo Published. Share: https://heyalice.help/download/ALICE-Setup.exe
pause
exit /b 0

:failed
echo.
echo ERROR: the upload failed. Scroll up for the first error.
pause
exit /b 1
