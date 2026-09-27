@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion
cd /d "%~dp0"

:: Запуск приложения Fluxion из исходников (то же, что FluxionBrowserLite.exe).
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 goto :no_python
"%PY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 goto :no_deps

"%PY%" -m desktop_browser
if errorlevel 1 pause
exit /b 0

:no_deps
echo [ОШИБКА] Зависимости не установлены. Сначала запустите fluxion-setup.bat.
pause
exit /b 1

:no_python
echo [ОШИБКА] Python не найден. Сначала запустите fluxion-setup.bat.
pause
exit /b 1
