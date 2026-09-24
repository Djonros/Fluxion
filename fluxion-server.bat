@echo off
chcp 65001 >nul 2>&1
title Fluxion Server
cd /d "%~dp0"

:: Quick dependency check
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Run fluxion-deploy.bat first.
    pause
    exit /b 1
)

:: Check FastAPI
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo Installing server dependencies...
    pip install fastapi uvicorn[standard] --quiet
)

echo.
echo  Fluxion API Server
echo  ─────────────────────────────────────────
echo  URL:   http://localhost:8765
echo  Docs:  http://localhost:8765/docs
echo  Health: http://localhost:8765/api/health
echo  ─────────────────────────────────────────
echo.
python -m uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8765
pause
