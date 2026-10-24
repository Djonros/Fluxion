@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - нормоконтроль чертежа AutoCAD
cd /d "%~dp0"

:: ============================================================================
::  Нормоконтроль чертежа AutoCAD (DWG/DXF) по ЕСКД или СПДС
::
::  fluxion-acad.bat "чертёж.dwg"                 замечания и отчёт в Excel
::  fluxion-acad.bat "чертёж.dwg" --check         только замечания
::  fluxion-acad.bat "чертёж.dwg" --norm СПДС     нормы вручную
::  DWG открывается через AutoCAD (копия, только для чтения) или ODA File Converter.
::  Отчёты сохраняются в Документы\Fluxion CAD
:: ============================================================================

set "PYTHONUTF8=1"
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" -c "import win32com.client, ezdxf, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo Устанавливаю pywin32, ezdxf и openpyxl...
    "%PY%" -m pip install -q pywin32 ezdxf openpyxl
)

if "%~1"=="" (
    echo Укажите чертёж: fluxion-acad.bat "C:\путь\чертёж.dwg"
) else (
    "%PY%" -m cad --acad %*
)
echo.
pause
