@echo off
chcp 65001 >nul 2>&1
title Fluxion — SearXNG Setup
cd /d "%~dp0"

echo ════════════════════════════════════════════════
echo   Fluxion — Web Search (SearXNG) Setup
echo ════════════════════════════════════════════════
echo.

:: ── Check Docker ───────────────────────────────────────────────
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker not found or not running!
    echo.
    echo   1. Install Docker Desktop: https://docker.com/products/docker-desktop
    echo   2. Start Docker Desktop
    echo   3. Run this script again
    echo.
    pause
    exit /b 1
)
echo [OK] Docker is running

:: ── Check if container already exists ──────────────────────────
echo.
echo Checking existing SearXNG container...
docker ps -a --filter "name=vibe-coder-searxng" --format "{{.Names}}" 2>nul | findstr "searxng" >nul
if not errorlevel 1 (
    echo SearXNG container exists. Starting...
    docker start vibe-coder-searxng >nul 2>&1
    echo [OK] SearXNG started on http://localhost:8080
    timeout /t 2 >nul
    pause
    exit /b 0
)

:: ── Create SearXNG container ───────────────────────────────────
echo.
echo Creating SearXNG container...
echo This will download the image (~200 MB) on first run.
echo.

:: Prepare data dir
if not exist "data\searxng" mkdir "data\searxng"

docker run -d ^
    --name vibe-coder-searxng ^
    -p 8080:8080 ^
    -e SEARXNG_BASE_URL=http://localhost:8080 ^
    -e SEARXNG_SECRET=%RANDOM%%RANDOM%%RANDOM% ^
    -v "%~dp0data\searxng:/etc/searxng" ^
    --restart unless-stopped ^
    searxng/searxng:latest

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create SearXNG container.
    echo         Trying PowerShell setup script instead...
    powershell -ExecutionPolicy Bypass -File scripts\setup_searxng.ps1
    pause
    exit /b 1
)

echo.
echo Waiting for SearXNG to start...
timeout /t 5 >nul

:: ── Verify ─────────────────────────────────────────────────────
echo Verifying...
curl -s "http://localhost:8080/search?q=test&format=json" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] SearXNG may need a few more seconds to start.
    echo          Check manually: http://localhost:8080
) else (
    echo [OK] SearXNG is running on http://localhost:8080
)

echo.
echo ════════════════════════════════════════════════
echo  SearXNG setup complete!
echo  Web search is now available in Fluxion.
echo ════════════════════════════════════════════════
pause
