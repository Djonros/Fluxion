@echo off
chcp 65001 >nul 2>&1
title Fluxion — VS Code Extension
cd /d "%~dp0"

echo ════════════════════════════════════════════════
echo   Fluxion — VS Code Extension Install
echo ════════════════════════════════════════════════
echo.

:: ── Check extension dir ────────────────────────────────────────
if not exist "extension\package.json" (
    echo [ERROR] Extension files not found in .\extension\
    pause
    exit /b 1
)
echo [OK] Extension found

:: ── Check VS Code ──────────────────────────────────────────────
where code >nul 2>&1
if errorlevel 1 (
    echo.
    echo [!] VS Code CLI not found on PATH.
    echo.
    echo   Option A: Open VS Code manually and press F5 in the extension/ folder
    echo           for Extension Development Host (debug mode).
    echo.
    echo   Option B: Add VS Code to PATH and re-run this script.
    echo.
    echo   Option C: Copy extension manually:
    echo.
    set /p choice="Copy to default extensions folder? (Y/N): "
    /i "%choice%"=="Y" goto copy
    pause
    exit /b 0
)

:copy
:: ── Copy to extensions folder ──────────────────────────────────
echo.
echo Installing to VS Code extensions folder...
set EXT_DIR=%USERPROFILE%\.vscode\extensions\fluxion

if exist "%EXT_DIR%" rmdir /s /q "%EXT_DIR%"
mkdir "%EXT_DIR%" 2>nul

xcopy /E /I /Q "extension\*" "%EXT_DIR%\" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy extension files.
    pause
    exit /b 1
)

echo [OK] Extension installed to %EXT_DIR%
echo.
echo ════════════════════════════════════════════════
echo  Extension installed!
echo.
echo  1. Restart VS Code
echo  2. Start Fluxion server: fluxion-server.bat
echo  3. Press Ctrl+Shift+P in VS Code
echo  4. Type "Fluxion" to see commands
echo ════════════════════════════════════════════════
pause
