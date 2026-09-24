@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

rem ── Fluxion: выпуск лицензионного ключа ─────────────────────────────────
rem Использование:
rem   issue-key.bat                          — интерактивный режим
rem   issue-key.bat <email> <1m^|3m^|1y^|days:N^|perpetual> [DEVICE-CODE]
rem Примеры:
rem   issue-key.bat client@mail.com 1m
rem   issue-key.bat client@mail.com 1y AB12-CD34-EF56-7890
rem   issue-key.bat client@mail.com days:14
rem   issue-key.bat client@mail.com perpetual
rem Ключ печатается на экран и сохраняется в keys\issued\<email>-<stamp>.key
rem Журнал выдачи: keys\issued\registry.jsonl
rem Для кода устройства клиента: python -m licensing.fingerprint

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "PYTHON=python"
where %PYTHON% >nul 2>nul || (echo Python not found in PATH & exit /b 1)

if not exist "keys\private.pem" (
    echo Private key not found: keys\private.pem
    exit /b 1
)

set "EMAIL=%~1"
set "TERM=%~2"
set "DEVICE=%~3"

if "%EMAIL%"=="" (
    set /p "EMAIL=Email клиента: "
)
if "%EMAIL%"=="" (echo Email не указан & exit /b 1)

if "%TERM%"=="" (
    echo Срок действия:
    echo   1  - 1 месяц
    echo   3  - 3 месяца
    echo   12 - 1 год
    echo   0  - бессрочный
    set /p "TERM=Выбор [1/3/12/0]: "
)

set "EXPIRE_ARGS="
if "%TERM%"=="1"  set "EXPIRE_ARGS=--months 1"
if "%TERM%"=="3"  set "EXPIRE_ARGS=--months 3"
if "%TERM%"=="12" set "EXPIRE_ARGS=--years 1"
if "%TERM%"=="1m" set "EXPIRE_ARGS=--months 1"
if "%TERM%"=="3m" set "EXPIRE_ARGS=--months 3"
if "%TERM%"=="1y" set "EXPIRE_ARGS=--years 1"
if "%TERM%"=="0"  set "EXPIRE_ARGS="
if "%TERM%"=="perpetual" set "EXPIRE_ARGS="
if "!EXPIRE_ARGS!"=="" if "%TERM:~0,5%"=="days:" set "EXPIRE_ARGS=--days %TERM:~5%"

if not "%DEVICE%"=="" set "EXTRA_ARGS=--device %DEVICE%"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%I"
set "SAFE_EMAIL=%EMAIL:@=at%"
set "SAFE_EMAIL=%SAFE_EMAIL:.=-%"
set "OUTFILE=keys\issued\%SAFE_EMAIL%-%STAMP%.key"

if not exist "keys\issued" mkdir "keys\issued"

echo Выпуск ключа: %EMAIL% срок=[%TERM%] устройство=[%DEVICE%]
%PYTHON% -m licensing.issuer --email "%EMAIL%" %EXPIRE_ARGS% %EXTRA_ARGS% > "%OUTFILE%.tmp" 2> "%TEMP%\fluxion-issue.err"
if errorlevel 1 (
    type "%TEMP%\fluxion-issue.err"
    del /q "%OUTFILE%.tmp" >nul 2>nul
    exit /b 1
)

move /y "%OUTFILE%.tmp" "%OUTFILE%" >nul
set /p "KEY="<nul
for /f "usebackq delims=" %%K in ("%OUTFILE%") do set "KEY=%%K"

echo.
echo ──────────────────────────────────────────────
echo Лицензионный ключ (сохранён в %OUTFILE%):
echo.
echo %KEY%
echo.
echo Код устройства этого ПК:
%PYTHON% -m licensing.fingerprint
echo ──────────────────────────────────────────────
endlocal
