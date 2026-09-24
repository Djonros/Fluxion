@echo off
chcp 65001 >nul 2>&1
title Fluxion Desktop
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run fluxion-deploy.bat first.
    pause
    exit /b 1
)

python -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo Installing desktop dependencies...
    python -m pip install "PySide6>=6.10"
    if errorlevel 1 (
        echo [ERROR] Failed to install PySide6.
        pause
        exit /b 1
    )
)

python -m desktop
if errorlevel 1 pause
