@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion CLI
cd /d "%~dp0"

:: Fluxion в терминале: чат, RAG, агент.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 goto :no_python
echo Starting Fluxion CLI...
echo.
"%PY%" -m cli.app
pause
exit /b 0

:no_python
echo [ОШИБКА] Python не найден. Сначала запустите fluxion-setup.bat.
pause
exit /b 1
