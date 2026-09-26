@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - обновление
cd /d "%~dp0"

:: ============================================================================
::  Обновление Fluxion из архива Fluxion-main-fixed.zip
::
::  Использование:
::    fluxion-update.bat                  - ищет архив рядом с батником или в Загрузках
::    fluxion-update.bat путь\к\архиву.zip - или перетащите архив на батник мышкой
::    fluxion-update.bat --rollback       - вернуть версию из последней резервной копии
::
::  Что делает:
::    1. резервная копия кода (без моделей, data, venv, .git) в соседнюю папку
::    2. распаковка архива во временную папку
::    3. копирование поверх с заменой; ваши config.yaml, repos.yaml и
::       continue_config.json НЕ перезаписываются; .env, модели и venv не трогаются
::    4. проверка: валидация бенчмарка и тесты агента
::    5. при ошибке - предложение откатить всё обратно
:: ============================================================================

set "APPDIR=%~dp0"
set "APPDIR=%APPDIR:~0,-1%"
for %%I in ("%APPDIR%") do set "PARENT=%%~dpI"
for %%I in ("%APPDIR%") do set "APPNAME=%%~nxI"
set "BACKUP_PREFIX=%PARENT%%APPNAME%-backup-"
set "ZIPNAME=Fluxion-main-fixed.zip"

echo ============================================================
echo    FLUXION - обновление
echo ============================================================
echo.

if not exist "%APPDIR%\orchestrator\agent.py" (
    echo [ОШИБКА] Батник должен лежать в корневой папке Fluxion,
    echo          там же, где папки orchestrator, core, server.
    goto :fail
)

if /i "%~1"=="--rollback" goto :rollback_latest

:: ── Python ──────────────────────────────────────────────────────────────────
set "PY="
if exist "%APPDIR%\.venv\Scripts\python.exe" set "PY=%APPDIR%\.venv\Scripts\python.exe"
if not defined PY if exist "%APPDIR%\venv\Scripts\python.exe" set "PY=%APPDIR%\venv\Scripts\python.exe"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

:: ── Архив ───────────────────────────────────────────────────────────────────
set "ZIP="
if not "%~1"=="" set "ZIP=%~f1"
if not defined ZIP if exist "%APPDIR%\%ZIPNAME%" set "ZIP=%APPDIR%\%ZIPNAME%"
if not defined ZIP if exist "%USERPROFILE%\Downloads\%ZIPNAME%" set "ZIP=%USERPROFILE%\Downloads\%ZIPNAME%"
if not defined ZIP (
    echo [ОШИБКА] Не найден архив %ZIPNAME%.
    echo          Положите его рядом с этим батником или в папку Загрузки,
    echo          либо перетащите архив мышкой на fluxion-update.bat.
    goto :fail
)
if exist "%ZIP%" goto :zip_ok
echo [ОШИБКА] Файл не найден: %ZIP%
goto :fail
:zip_ok
echo Архив:     %ZIP%
echo Программа: %APPDIR%
echo.
echo Перед продолжением закройте Fluxion: приложение, сервер и CLI.
choice /c YN /m "Начать обновление"
if errorlevel 2 goto :cancelled

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%t"
if not defined TS set "TS=%RANDOM%%RANDOM%"
set "BACKUP=%BACKUP_PREFIX%%TS%"
set "TMPX=%TEMP%\fluxion-update-%TS%"
set "SRC=%TMPX%\Fluxion-main"

:: ── 1. Резервная копия ──────────────────────────────────────────────────────
echo.
echo [1/5] Резервная копия кода...
robocopy "%APPDIR%" "%BACKUP%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP ^
    /XD data .venv venv env .git dist build node_modules __pycache__ .fluxion-backup results logs ^
    /XF *.gguf *.safetensors *.bin >nul
if errorlevel 8 (
    echo       [ОШИБКА] Не удалось сделать резервную копию.
    goto :fail
)
echo       Готово: %BACKUP%

:: ── 2. Распаковка ───────────────────────────────────────────────────────────
echo [2/5] Распаковка архива...
if exist "%TMPX%" rmdir /s /q "%TMPX%"
powershell -NoProfile -Command "Expand-Archive -LiteralPath $env:ZIP -DestinationPath $env:TMPX -Force"
if not exist "%SRC%\orchestrator\agent.py" (
    echo       [ОШИБКА] Архив повреждён или это не архив Fluxion.
    goto :fail_cleanup
)
echo       Готово

:: Запоминаем файлы, которых раньше не было, чтобы откат мог их удалить.
powershell -NoProfile -Command "$s=$env:SRC; Get-ChildItem -LiteralPath $s -Recurse -File | ForEach-Object { $_.FullName.Substring($s.Length+1) } | Where-Object { -not (Test-Path -LiteralPath (Join-Path $env:APPDIR $_)) } | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $env:BACKUP '_new_files.txt')"

:: ── 3. Копирование ──────────────────────────────────────────────────────────
echo [3/5] Копирование новых файлов...
robocopy "%SRC%" "%APPDIR%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP ^
    /XF config.yaml repos.yaml continue_config.json >nul
if errorlevel 8 (
    echo       [ОШИБКА] Часть файлов не скопировалась. Возможно, Fluxion ещё запущен.
    goto :offer_rollback
)
:: Если своих конфигов не было - берём из архива.
if not exist "%APPDIR%\config\config.yaml" copy /y "%SRC%\config\config.yaml" "%APPDIR%\config\" >nul
if not exist "%APPDIR%\config\repos.yaml" copy /y "%SRC%\config\repos.yaml" "%APPDIR%\config\" >nul
if not exist "%APPDIR%\config\continue_config.json" copy /y "%SRC%\config\continue_config.json" "%APPDIR%\config\" >nul
rmdir /s /q "%TMPX%" 2>nul
echo       Готово. Ваши настройки в config сохранены.

:: ── 4-5. Проверка ───────────────────────────────────────────────────────────
if not defined PY (
    echo.
    echo [!] Python не найден - автоматическая проверка пропущена.
    goto :done
)

echo [4/5] Проверка набора задач бенчмарка...
"%PY%" -m eval.agent_bench --validate
if errorlevel 1 (
    echo       [ОШИБКА] Проверка не прошла.
    goto :offer_rollback
)

echo [5/5] Тесты агента...
"%PY%" -m pytest --version >nul 2>&1
if errorlevel 1 (
    echo       [!] pytest не установлен - тесты пропущены.
    echo           Установить: "%PY%" -m pip install pytest
    goto :done
)
"%PY%" -m pytest -q -p no:cacheprovider tests\test_agent_regressions.py tests\test_agent_structured.py tests\test_agent_bench.py tests\test_phase7_agent.py tests\test_phase11_git.py
if errorlevel 1 (
    echo       [ОШИБКА] Часть тестов не прошла.
    goto :offer_rollback
)
goto :done

:: ── Откат ───────────────────────────────────────────────────────────────────
:offer_rollback
echo.
choice /c YN /m "Откатить обновление и вернуть прежнюю версию"
if errorlevel 2 goto :keep_update
goto :restore

:keep_update
echo Оставлено как есть. Резервная копия: %BACKUP%
echo Откатить позже: fluxion-update.bat --rollback
goto :fail

:rollback_latest
set "BACKUP="
for /f "delims=" %%d in ('dir /b /ad /o-n "%BACKUP_PREFIX%*" 2^>nul') do (
    if not defined BACKUP set "BACKUP=%PARENT%%%d"
)
if not defined BACKUP (
    echo [ОШИБКА] Резервные копии не найдены рядом с папкой программы.
    goto :fail
)
echo Будет восстановлена копия: %BACKUP%
echo Закройте Fluxion перед продолжением.
choice /c YN /m "Восстановить"
if errorlevel 2 goto :cancelled

:restore
echo.
echo Восстановление из %BACKUP% ...
if exist "%BACKUP%\_new_files.txt" (
    powershell -NoProfile -Command "Get-Content -Encoding UTF8 -LiteralPath (Join-Path $env:BACKUP '_new_files.txt') | ForEach-Object { $p = Join-Path $env:APPDIR $_; if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force } }"
)
robocopy "%BACKUP%" "%APPDIR%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XF _new_files.txt >nul
if errorlevel 8 goto :restore_failed
if exist "%TMPX%" rmdir /s /q "%TMPX%" 2>nul
echo Прежняя версия восстановлена.
goto :end_pause

:restore_failed
echo [ОШИБКА] Восстановление не завершилось. Скопируйте файлы из
echo          %BACKUP%
echo          в папку программы вручную.
goto :fail

:: ── Итог ────────────────────────────────────────────────────────────────────
:done
echo.
echo ============================================================
echo    Обновление установлено.
echo ============================================================
echo  Резервная копия:  %BACKUP%
echo  Откатить:         fluxion-update.bat --rollback
echo.
echo  Дальше:
echo   - пересоберите exe скриптами из папки scripts,
echo     иначе в собранной программе останется старый агент;
echo   - замер качества агента: python -m eval.agent_bench --limit 3 --modes json
echo     подробности в eval\agent_bench\README.md
goto :end_pause

:fail_cleanup
if exist "%TMPX%" rmdir /s /q "%TMPX%" 2>nul
:fail
echo.
echo Обновление не выполнено.
pause
exit /b 1

:cancelled
echo Отменено.
pause
exit /b 0

:end_pause
echo.
pause
exit /b 0
