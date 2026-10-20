@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - деталь в КОМПАС-3D
cd /d "%~dp0"

:: ============================================================================
::  Прототип: деталь в КОМПАС-3D по описанию
::
::  fluxion-kompas.bat --demo                         все шаблоны на примерах
::  fluxion-kompas.bat "втулка 25 на 16 длиной 30"    по описанию (нужна модель)
::  fluxion-kompas.bat --spec деталь.json             из готового JSON
::  Детали сохраняются в Документы\Fluxion CAD
:: ============================================================================

set "PYTHONUTF8=1"
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" -c "import win32com.client" >nul 2>&1
if errorlevel 1 (
    echo Устанавливаю pywin32...
    "%PY%" -m pip install -q pywin32
)

if "%~1"=="" (
    "%PY%" -m cad --demo
) else (
    "%PY%" -m cad %*
)
echo.
pause
