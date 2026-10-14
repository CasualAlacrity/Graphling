@echo off
REM Upload the installer to the Hetzner box so pilots can download it from
REM   https://heyalice.help/download/ALICE-Setup.exe          (always the newest)
REM   https://heyalice.help/download/ALICE-Setup-<version>.exe (that exact build)
REM
REM Run scripts\windows\build-installer.bat first. Needs, once:
REM   - an SSH key on this PC at SSH_KEY below whose public half is in the box's
REM     authorized_keys (docs/deploy.md, "Installer downloads")
REM   - the one-time nginx setup in docs/deploy.md, "Installer downloads"
REM
REM Uses Windows' built-in OpenSSH (ssh/scp), nothing to install.

cd /d "%~dp0..\.."

REM -- Edit these once --------------------------------------------------------
set PROD_HOST=168.119.234.226
set PROD_USER=root
set SSH_KEY=%USERPROFILE%\.ssh\alice_publish
set DOWNLOAD_DIR=/var/www/alice-downloads
set LATEST_SNIPPET=/etc/nginx/snippets/alice-latest.conf
REM ---------------------------------------------------------------------------

if not exist "%SSH_KEY%" (
    echo SSH key not found at %SSH_KEY%
    echo Make one there - see docs/deploy.md, "Installer downloads" - or edit SSH_KEY above.
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
REM half-uploaded installer. The stable link, ALICE-Setup.exe, is then pointed at it: a
REM one-line nginx snippet redirecting to the versioned file, so browsers save it as
REM ALICE-Setup-<version>.exe rather than piling up "ALICE-Setup (7).exe" in Downloads.
ssh -i "%SSH_KEY%" %PROD_USER%@%PROD_HOST% "mkdir -p %DOWNLOAD_DIR% /etc/nginx/snippets"
if errorlevel 1 goto failed
scp -i "%SSH_KEY%" "%INSTALLER%" %PROD_USER%@%PROD_HOST%:%DOWNLOAD_DIR%/ALICE-Setup-%APP_VERSION%.exe.part
if errorlevel 1 goto failed

ssh -i "%SSH_KEY%" %PROD_USER%@%PROD_HOST% "cd %DOWNLOAD_DIR% && mv ALICE-Setup-%APP_VERSION%.exe.part ALICE-Setup-%APP_VERSION%.exe && rm -f ALICE-Setup.exe && echo 'return 302 /download/ALICE-Setup-%APP_VERSION%.exe;' > %LATEST_SNIPPET% && nginx -t && systemctl reload nginx && ls -lh"
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
