@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - обновление

:: cmd reads a running .bat from disk by byte offset. Step 3 copies a new
:: fluxion-update.bat over this very file, so we first re-launch from a temp
:: copy; the copy in the program folder can then be replaced safely.
if /i "%~1"=="--from-temp" goto :from_temp
set "RUNNER=%TEMP%\fluxion-update-runner.bat"
copy /y "%~f0" "%RUNNER%" >nul
if errorlevel 1 goto :no_temp_copy
"%RUNNER%" --from-temp "%~dp0." %*
:no_temp_copy
echo [ОШИБКА] Не удалось скопировать батник во временную папку %TEMP%.
pause
exit /b 1

:from_temp
set "APPDIR=%~f2"
shift
shift
cd /d "%APPDIR%"

:: ============================================================================
::  Обновление Fluxion из архива Fluxion-main-fixed.zip
::
::  Использование:
::    fluxion-update.bat                  - ищет архив рядом с батником или в Загрузках
::    fluxion-update.bat путь\к\архиву.zip - или перетащите архив на батник мышкой
::    fluxion-update.bat --rollback       - вернуть версию из последней резервной копии
::    fluxion-update.bat --build-only     - только пересобрать exe, без обновления
::
::  Параметры сборки, чтобы не отвечать на вопрос в конце:
::    --build        пересобрать Lite         --build-full   пересобрать Full
::    --no-build     не пересобирать
::
::  Что делает:
::    1. резервная копия кода (без моделей, data, venv, .git) в соседнюю папку
::    2. распаковка архива во временную папку
::    3. копирование поверх с заменой; ваши config.yaml, repos.yaml и
::       continue_config.json НЕ перезаписываются; .env, модели и venv не трогаются
::    4. проверка: валидация бенчмарка и тесты агента
::    5. при ошибке - предложение откатить всё обратно
::    6. пересборка exe: Lite или Full, через scripts\build_lite.ps1 / build_full.ps1
:: ============================================================================

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

set "MODE=update"
set "ARG_ZIP="
set "BUILD_CHOICE="
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--rollback" (set "MODE=rollback" & shift & goto :parse_args)
if /i "%~1"=="--build-only" (set "MODE=build" & shift & goto :parse_args)
if /i "%~1"=="--build" (set "BUILD_CHOICE=lite" & shift & goto :parse_args)
if /i "%~1"=="--build-full" (set "BUILD_CHOICE=full" & shift & goto :parse_args)
if /i "%~1"=="--no-build" (set "BUILD_CHOICE=none" & shift & goto :parse_args)
set "ARG_ZIP=%~f1"
shift
goto :parse_args
:args_done
if "%MODE%"=="rollback" goto :rollback_latest

:: ── Python ──────────────────────────────────────────────────────────────────
set "PY="
if exist "%APPDIR%\.venv\Scripts\python.exe" set "PY=%APPDIR%\.venv\Scripts\python.exe"
if not defined PY if exist "%APPDIR%\venv\Scripts\python.exe" set "PY=%APPDIR%\venv\Scripts\python.exe"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if "%MODE%"=="build" goto :build_only

:: ── Архив ───────────────────────────────────────────────────────────────────
set "ZIP="
if defined ARG_ZIP set "ZIP=%ARG_ZIP%"
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
echo [1/6] Резервная копия кода...
robocopy "%APPDIR%" "%BACKUP%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP ^
    /XD data .venv venv env .git dist build node_modules __pycache__ .fluxion-backup results logs ^
    /XF *.gguf *.safetensors *.bin >nul
if errorlevel 8 (
    echo       [ОШИБКА] Не удалось сделать резервную копию.
    goto :fail
)
echo       Готово: %BACKUP%

:: ── 2. Распаковка ───────────────────────────────────────────────────────────
echo [2/6] Распаковка архива...
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
echo [3/6] Копирование новых файлов...
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
:: Files removed from the project in the new version (listed in
:: REMOVED_FILES.txt). Only inside the program folder; never config, data,
:: models, .git or venvs. They remain in the backup for --rollback.
if exist "%SRC%\REMOVED_FILES.txt" powershell -NoProfile -Command "$app=[IO.Path]::GetFullPath($env:APPDIR).TrimEnd('\'); $keep=@('config','data','models','.git','.venv','venv','env','results','logs','dist'); Get-Content -Encoding UTF8 -LiteralPath (Join-Path $env:SRC 'REMOVED_FILES.txt') | ForEach-Object { $l=$_.Trim(); if ($l -and -not $l.StartsWith('#')) { $first=($l -split '[\\/]')[0]; $p=[IO.Path]::GetFullPath((Join-Path $app $l)); if (($keep -notcontains $first.ToLower()) -and $p.StartsWith($app + '\', [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $p)) { Remove-Item -LiteralPath $p -Recurse -Force; Write-Host ('      removed: ' + $l) } } }"
rmdir /s /q "%TMPX%" 2>nul
echo       Готово. Ваши настройки в config сохранены.

:: ── 4-5. Проверка ───────────────────────────────────────────────────────────
if not defined PY (
    echo.
    echo [!] Python не найден - автоматическая проверка пропущена.
    goto :done
)

echo [4/6] Проверка набора задач бенчмарка...
"%PY%" -m eval.agent_bench --validate
if errorlevel 1 (
    echo       [ОШИБКА] Проверка не прошла.
    goto :offer_rollback
)

echo [5/6] Тесты агента...
"%PY%" -m pytest --version >nul 2>&1
if errorlevel 1 (
    echo       [!] pytest не установлен - тесты пропущены.
    echo           Установить: "%PY%" -m pip install pytest
    goto :build_offer
)
"%PY%" -m pytest -q -p no:cacheprovider tests\test_agent_regressions.py tests\test_agent_structured.py tests\test_agent_bench.py tests\test_phase7_agent.py tests\test_phase11_git.py tests\test_presentation.py tests\test_build_specs.py tests\test_release.py
if errorlevel 1 (
    echo       [ОШИБКА] Часть тестов не прошла.
    goto :offer_rollback
)
goto :build_offer

:: ── 6. Пересборка exe ───────────────────────────────────────────────────────
:build_only
if defined PY goto :build_only_py
echo [ОШИБКА] Python не найден - собрать exe нельзя.
goto :fail
:build_only_py
echo Пересборка exe без обновления кода.
echo Закройте FluxionBrowserLite.exe, если он запущен из папки dist.
goto :build_offer

:build_offer
if "%BUILD_CHOICE%"=="none" goto :done
if defined BUILD_CHOICE goto :run_build
echo.
echo [6/6] Пересборка exe. Без неё в собранной программе останется прежняя версия.
echo       1. Lite - программа, 5-15 минут
echo       2. Full - программа и офлайн-пакет для дообучения,
echo                 в первый раз скачивает несколько ГБ
echo       3. Не пересобирать сейчас
choice /c 123 /n /m "Выберите: "
if errorlevel 3 goto :done
if errorlevel 2 (set "BUILD_CHOICE=full") else (set "BUILD_CHOICE=lite")

:run_build
"%PY%" -m PyInstaller --version >nul 2>&1
if not errorlevel 1 goto :pyinstaller_ok
echo PyInstaller не установлен - он нужен для сборки exe.
choice /c YN /m "Установить PyInstaller сейчас"
if errorlevel 2 goto :done
"%PY%" -m pip install pyinstaller --quiet --disable-pip-version-check
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 goto :build_failed
:pyinstaller_ok

"%PY%" -c "import llama_cpp" >nul 2>&1
if not errorlevel 1 goto :engine_ok
echo [!] В этом Python нет llama-cpp-python: exe соберётся без встроенного движка
echo     и не сможет запускать модели. Установка движка:
echo     powershell -ExecutionPolicy Bypass -File scripts\install_llamacpp.ps1
choice /c YN /m "Всё равно собрать"
if errorlevel 2 goto :done
:engine_ok

set "SEVENZIP="
where 7z >nul 2>&1 && set "SEVENZIP=1"
if exist "%ProgramFiles%\7-Zip\7z.exe" set "SEVENZIP=1"
set "PACKARG="
if not defined SEVENZIP set "PACKARG=-NoPack"
if not defined SEVENZIP echo [!] 7-Zip не найден: exe будет собран, архив .7z для раздачи - нет.
if "%BUILD_CHOICE%"=="full" if not defined SEVENZIP echo [!] Для Full нужен 7-Zip - собираю только Lite.
if not defined SEVENZIP set "BUILD_CHOICE=lite"

set "FLUXION_PYTHON=%PY%"
echo.
echo Сборка Lite... это 5-15 минут, окно не закрывайте.
powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%\scripts\build_lite.ps1" %PACKARG%
if errorlevel 1 goto :build_failed
if not "%BUILD_CHOICE%"=="full" goto :build_check
echo.
echo Сборка Full поверх свежего Lite...
powershell -NoProfile -ExecutionPolicy Bypass -File "%APPDIR%\scripts\build_full.ps1" -SkipLiteBuild
if errorlevel 1 goto :build_failed

:build_check
set "EXE=%APPDIR%\dist\FluxionBrowserLite\FluxionBrowserLite.exe"
if not exist "%EXE%" goto :build_failed
set "BUILT=1"
if not exist "%APPDIR%\dist\FluxionBrowserLite\_internal\llama_cpp\lib\*.dll" set "NOENGINE=1"
goto :done

:build_failed
set "BUILD_FAILED=1"
echo.
echo [ОШИБКА] Сборка exe не удалась - причина в выводе выше.
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
if "%MODE%"=="build" echo    Пересборка exe
if not "%MODE%"=="build" echo    Обновление установлено.
echo ============================================================
if defined BACKUP echo  Резервная копия:  %BACKUP%
if defined BACKUP echo  Откатить код:     fluxion-update.bat --rollback
if defined BUILT echo  Новый exe:        %EXE%
if defined BUILT if "%BUILD_CHOICE%"=="full" echo  Архивы для раздачи: %APPDIR%\dist\artifacts
if defined BUILT if not defined PACKARG if not "%BUILD_CHOICE%"=="full" echo  Архив для раздачи: %APPDIR%\dist\artifacts
if defined NOENGINE echo  [!] В сборке нет библиотек llama.cpp - exe не сможет запускать модели.
if defined BUILD_FAILED echo  Сборка exe не удалась. Повторить: fluxion-update.bat --build-only
if not defined BUILT if not defined BUILD_FAILED echo  exe не пересобран. Пересобрать позже: fluxion-update.bat --build-only
echo.
echo  Проверка агента на модели: fluxion-test.bat, пункт 4
if defined BUILD_FAILED goto :end_fail
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

:end_fail
echo.
pause
exit /b 1
