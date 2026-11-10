@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - новая версия
cd /d "%~dp0"

:: ============================================================================
::  Поднять версию для выпуска
::
::    fluxion-version.bat 0.29.0    - версия и дата выпуска, раздел Unreleased
::                                    в CHANGELOG становится разделом версии,
::                                    заготовка RELEASE_NOTES_RU.md; потом по
::                                    желанию: коммит «Release vX.Y.Z», тесты,
::                                    сборка exe и установщика
::    fluxion-version.bat           - спросит номер версии
::
::  Тег и релиз на GitHub здесь не делаются: это fluxion-release.bat.
:: ============================================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\version.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
