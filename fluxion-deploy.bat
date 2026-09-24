@echo off
chcp 65001 >nul 2>&1
title Fluxion — Full Deploy
cd /d "%~dp0"

echo ════════════════════════════════════════════════════════════
echo                    FLUXION — FULL DEPLOY
echo              Local AI Coding Assistant (v0.1.0)
echo ════════════════════════════════════════════════════════════
echo.

:: ── Step 1: Python ─────────────────────────────────────────────
echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [FAIL] Python not found!
    echo          Download: https://www.python.org/downloads/
    echo          Check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo       Python %PYVER% — OK

:: ── Step 2: Dependencies ───────────────────────────────────────
echo [2/6] Installing Python dependencies...
pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo       [FAIL] Could not install requirements.txt
    pause
    exit /b 1
)
pip install fastapi uvicorn[standard] --quiet --disable-pip-version-check >nul 2>&1
echo       Dependencies — OK

:: ── Step 3: Ollama ─────────────────────────────────────────────
echo [3/6] Checking Ollama...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [!] Ollama not found.
    echo       Download from: https://ollama.com/download
    echo.
    echo       After install, run this script again.
    echo.
    choice /c YN /m "Continue without Ollama (CLI will not work)"
    if errorlevel 2 exit /b 1
) else (
    echo       Ollama — OK

    :: Check if server is running
    echo       Checking Ollama server...
    ollama list >nul 2>&1
    if errorlevel 1 (
        echo       Starting Ollama server...
        start "" ollama serve
        timeout /t 3 >nul
    )

    :: Pull model if needed
    echo       Checking model...
    ollama list 2>nul | findstr "qwen2.5-coder" >nul
    if errorlevel 1 (
        echo       Pulling qwen2.5-coder:7b-instruct (~4.7 GB)...
        echo       This may take 5-20 minutes depending on your connection.
        ollama pull qwen2.5-coder:7b-instruct
    ) else (
        echo       Model already installed — OK
    )
)

:: ── Step 4: SearXNG (optional) ─────────────────────────────────
echo [4/6] Checking SearXNG (web search, optional)...
docker info >nul 2>&1
if errorlevel 1 (
    echo       Docker not found — web search disabled
    echo       To enable: install Docker Desktop, then run fluxion-web.bat
) else (
    docker ps --filter "name=vibe-coder-searxng" --format "{{.Names}}" 2>nul | findstr "searxng" >nul
    if errorlevel 1 (
        echo       SearXNG container not running.
        echo       To start: run fluxion-web.bat
    ) else (
        echo       SearXNG — OK
    )
)

:: ── Step 5: Verify ─────────────────────────────────────────────
echo [5/6] Verifying installation...
python -c "from core.config import Settings; s=Settings.load(); print('       Config loaded:', s.model)" 2>nul
if errorlevel 1 (
    echo       [FAIL] Cannot load Fluxion config
    pause
    exit /b 1
)
echo       Verification — OK

:: ── Step 6: Done — choose mode ─────────────────────────────────
echo [6/6] Setup complete!
echo.
echo ════════════════════════════════════════════════════════════
echo  What do you want to run?
echo ════════════════════════════════════════════════════════════
echo.
echo   1. CLI REPL       — terminal chat, RAG, agent (fluxion.bat)
echo   2. API Server     — FastAPI on :8765 (fluxion-server.bat)
echo   3. Both           — server in background + CLI
echo   4. Run tests      — pytest verification
echo   5. Exit
echo.
choice /c 12345 /m "Choose"

if errorlevel 5 exit /b 0
if errorlevel 4 (
    echo.
    echo Running tests...
    python -m pytest tests/ -v --tb=short -q
    pause
    exit /b 0
)
if errorlevel 3 (
    echo.
    echo Starting API server in background...
    start "Fluxion Server" cmd /c "cd /d "%~dp0" && python -m uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8765"
    timeout /t 2 >nul
    echo Starting CLI...
    python -m cli.app
    pause
    exit /b 0
)
if errorlevel 2 (
    echo.
    call fluxion-server.bat
    exit /b 0
)
if errorlevel 1 (
    echo.
    call fluxion.bat
    exit /b 0
)
