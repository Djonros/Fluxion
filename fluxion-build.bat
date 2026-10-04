@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - пересборка exe
cd /d "%~dp0"

:: ============================================================================
::  Пересборка exe из текущего кода, без обновления
::
::    fluxion-build.bat           - спросит, что собрать: Lite или Full
::    fluxion-build.bat --lite    - собрать Lite без вопроса
::    fluxion-build.bat --full    - собрать Full без вопроса
::
::  Сборку выполняет fluxion-update.bat --build-only: те же проверки перед
::  сборкой (PyInstaller, движок llama.cpp, 7-Zip) и после неё (exe на месте,
::  библиотеки движка внутри). Результат: dist\FluxionBrowserLite\, архивы для
::  раздачи - в dist\artifacts\.
:: ============================================================================

if not exist "fluxion-update.bat" goto :no_updater
if not exist "scripts\build_lite.ps1" goto :no_scripts

set "BUILD_ARG="
if /i "%~1"=="--lite" set "BUILD_ARG=--build"
if /i "%~1"=="--full" set "BUILD_ARG=--build-full"
if "%~1"=="" goto :run
if defined BUILD_ARG goto :run
echo Неизвестный параметр: %~1
echo Доступно: --lite, --full или без параметров.
pause
exit /b 2

:run
echo Перед сборкой закройте FluxionBrowserLite.exe, если он запущен из папки dist.
call "%~dp0fluxion-update.bat" --build-only %BUILD_ARG%
exit /b %errorlevel%

:no_updater
echo [ОШИБКА] Не найден fluxion-update.bat - он выполняет сборку.
echo          Батник должен лежать в корневой папке Fluxion.
pause
exit /b 1

:no_scripts
echo [ОШИБКА] Не найдены скрипты сборки в папке scripts.
echo          Батник должен лежать в корневой папке Fluxion.
pause
exit /b 1
