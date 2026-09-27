@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion Server
cd /d "%~dp0"

:: Локальный API-сервер для расширения VS Code (только 127.0.0.1).
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 goto :no_python
"%PY%" -c "import fastapi, sqlalchemy" >nul 2>&1
if not errorlevel 1 goto :run
echo Устанавливаю зависимости сервера...
"%PY%" -m pip install -r server\requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :deps_failed

:run
echo.
echo  Fluxion API Server
echo  -----------------------------------------
echo  URL:    http://localhost:8765
echo  Docs:   http://localhost:8765/docs
echo  Health: http://localhost:8765/api/health
echo  -----------------------------------------
echo.
"%PY%" -m uvicorn server.app:create_app --factory --host 127.0.0.1 --port 8765
pause
exit /b 0

:deps_failed
echo [ОШИБКА] Зависимости сервера не установились - см. вывод выше.
pause
exit /b 1

:no_python
echo [ОШИБКА] Python не найден. Сначала запустите fluxion-setup.bat.
pause
exit /b 1
