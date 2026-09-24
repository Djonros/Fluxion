@echo off
chcp 65001 >nul 2>&1
title Fluxion
cd /d "%~dp0"

:: Quick dependency check
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run fluxion-deploy.bat first.
    pause
    exit /b 1
)

echo Starting Fluxion CLI...
echo.
python -m cli.app
pause
