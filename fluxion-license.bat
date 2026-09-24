@echo off
setlocal
chcp 65001 >nul 2>&1
title Fluxion License Issuer
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run fluxion-deploy.bat first.
    pause
    exit /b 1
)

if not exist "keys\private.pem" (
    echo [ERROR] Private key not found: keys\private.pem
    echo Restore the owner's private Ed25519 key before issuing licenses.
    pause
    exit /b 1
)

echo ============================================
echo   Fluxion Pro License Issuer
echo ============================================
echo.

set "EMAIL="
set /p "EMAIL=Customer email: "
if not defined EMAIL (
    echo [ERROR] Email is required.
    pause
    exit /b 1
)

set "EXPIRES="
set /p "EXPIRES=Expiry YYYY-MM-DD (Enter = perpetual): "

set "KEY_FILE=%TEMP%\fluxion-license-%RANDOM%-%RANDOM%.txt"
if defined EXPIRES (
    python -m licensing.issuer --email "%EMAIL%" --plan pro --expires "%EXPIRES%" > "%KEY_FILE%"
) else (
    python -m licensing.issuer --email "%EMAIL%" --plan pro > "%KEY_FILE%"
)

if errorlevel 1 (
    if exist "%KEY_FILE%" del "%KEY_FILE%" >nul 2>&1
    echo.
    echo [ERROR] License generation failed.
    pause
    exit /b 1
)

echo.
echo License key for %EMAIL%:
echo.
type "%KEY_FILE%"
echo.

where clip >nul 2>&1
if not errorlevel 1 (
    type "%KEY_FILE%" | clip
    echo [OK] License key copied to clipboard.
)

del "%KEY_FILE%" >nul 2>&1
echo Give this key to the customer for: /license activate ^<key^>
echo Keep keys\private.pem secret.
echo.
pause
endlocal
