@echo off
title Fluxion Setup
cd /d "%~dp0"

echo ============================================
echo   Fluxion — First-time Setup
echo ============================================
echo.

:: ── Check Python ──────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ from https://python.org
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python %PYVER%

:: ── Install dependencies ──────────────────────────
echo.
echo Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.txt
    pause
    exit /b 1
)
echo [OK] Dependencies installed

:: ── Check Ollama ──────────────────────────────────
ollama --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNING] Ollama not found!
    echo           Download from https://ollama.com
    echo           After install, run: ollama pull qwen2.5-coder:7b-instruct
    echo.
) else (
    echo [OK] Ollama found
)

:: ── Pull model if needed ──────────────────────────
echo.
echo Checking model...
ollama list 2>nul | findstr "qwen2.5-coder" >nul
if errorlevel 1 (
    echo Model not found. Pulling qwen2.5-coder:7b-instruct...
    echo This may take a while (first time only, ~4.7 GB)...
    ollama pull qwen2.5-coder:7b-instruct
) else (
    echo [OK] Model already installed
)

:: ── Done ──────────────────────────────────────────
echo.
echo ============================================
echo   Setup complete!
echo   Run fluxion.bat to start Fluxion.
echo ============================================
pause
