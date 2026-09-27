@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - расширение VS Code
cd /d "%~dp0"

:: Устанавливает расширение Fluxion в VS Code (копирует папку extension в
:: %USERPROFILE%\.vscode\extensions\fluxion). Расширению нужен запущенный
:: fluxion-server.bat.

echo ============================================================
echo    FLUXION - установка расширения VS Code
echo ============================================================
echo.
if not exist "extension\package.json" goto :no_extension

set "EXT_DIR=%USERPROFILE%\.vscode\extensions\fluxion"
where code >nul 2>&1
if not errorlevel 1 goto :install
echo [!] Команда code не найдена в PATH - это не мешает установке.
echo     Расширение будет скопировано в папку расширений VS Code.
echo.
choice /c YN /m "Установить"
if errorlevel 2 goto :cancelled

:install
echo Установка в %EXT_DIR% ...
if exist "%EXT_DIR%" rmdir /s /q "%EXT_DIR%"
mkdir "%EXT_DIR%" 2>nul
xcopy /E /I /Q /Y "extension\*" "%EXT_DIR%\" >nul
if errorlevel 1 goto :copy_failed
if not exist "%EXT_DIR%\src\extension.js" goto :copy_failed

echo.
echo ============================================================
echo    Расширение установлено
echo ============================================================
echo    1. Перезапустите VS Code
echo    2. Запустите сервер: fluxion-server.bat
echo    3. В VS Code нажмите Ctrl+Shift+P и введите Fluxion
echo.
echo    Отладка расширения: откройте папку extension в VS Code и нажмите F5.
echo.
pause
exit /b 0

:no_extension
echo [ОШИБКА] Не найдена папка extension с файлом package.json.
pause
exit /b 1

:copy_failed
echo [ОШИБКА] Не удалось скопировать файлы расширения.
pause
exit /b 1

:cancelled
echo Отменено.
pause
exit /b 0
