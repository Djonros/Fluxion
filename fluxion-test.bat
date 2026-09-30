@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - тестирование
cd /d "%~dp0"

:: ============================================================================
::  Тестирование Fluxion
::
::  Меню:           fluxion-test.bat
::  Без меню:       fluxion-test.bat quick     - быстрая проверка агента
::                  fluxion-test.bat groups    - все тесты по группам со сводкой
::                  fluxion-test.bat ci        - все тесты одним прогоном, как в CI
::                  fluxion-test.bat smoke     - агент на реальной модели, 3 задачи
::                  fluxion-test.bat bench     - полный замер качества агента
::
::  Код выхода без меню: 0 - всё прошло, 1 - есть падения.
:: ============================================================================

set "PYTHONUTF8=1"
set "QT_QPA_PLATFORM=offscreen"

if not exist "orchestrator\agent.py" (
    echo [ОШИБКА] Батник должен лежать в корневой папке Fluxion.
    pause
    exit /b 1
)

:: ── Python ──────────────────────────────────────────────────────────────────
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [ОШИБКА] Python не найден. Установите Python 3.11+ и отметьте Add Python to PATH.
    pause
    exit /b 1
)

"%PY%" -m pytest --version >nul 2>&1
if not errorlevel 1 goto :pytest_ok
echo pytest не установлен - он нужен для тестов.
choice /c YN /m "Установить pytest сейчас"
if errorlevel 2 exit /b 1
"%PY%" -m pip install pytest --quiet --disable-pip-version-check
"%PY%" -m pytest --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Не удалось установить pytest.
    pause
    exit /b 1
)
:pytest_ok

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%t"
if not defined TS set "TS=%RANDOM%"
set "LOGROOT=%~dp0logs\tests"

:: ── Режим без меню ──────────────────────────────────────────────────────────
set "INTERACTIVE=1"
if "%~1"=="" goto :menu
set "INTERACTIVE="
if /i "%~1"=="quick"  goto :quick
if /i "%~1"=="groups" goto :groups
if /i "%~1"=="ci"     goto :ci
if /i "%~1"=="smoke"  goto :smoke
if /i "%~1"=="bench"  goto :bench
echo Неизвестный режим: %~1
echo Доступно: quick, groups, ci, smoke, bench
exit /b 2

:: ── Меню ────────────────────────────────────────────────────────────────────
:menu
echo.
echo ============================================================
echo    FLUXION - тестирование
echo ============================================================
echo    Python: %PY%
echo.
echo    Тесты кода, модель не нужна:
echo      1. Быстрая проверка агента            1-2 мин
echo      2. Все тесты по группам со сводкой    5-10 мин
echo      3. Все тесты одним прогоном, как в CI
echo.
echo    Проверка агента на реальной модели из config\config.yaml:
echo      4. Пробный прогон, 3 задачи           5-10 мин на CPU
echo      5. Полный замер качества              ~2,5 часа на CPU, можно прерывать
echo      6. Открыть последний отчёт замера
echo.
echo      0. Выход
echo.
choice /c 1234560 /n /m "Выберите пункт: "
if errorlevel 7 exit /b 0
if errorlevel 6 goto :report
if errorlevel 5 goto :bench
if errorlevel 4 goto :smoke
if errorlevel 3 goto :ci
if errorlevel 2 goto :groups
goto :quick

:: ── 1. Быстрая проверка агента ──────────────────────────────────────────────
:quick
call :init_run quick
call :run_group agent "Агент: инструменты, форматы, git, бенчмарк" "yaml, httpx, dotenv" "tests\test_agent_regressions.py tests\test_agent_structured.py tests\test_agent_bench.py tests\test_phase7_agent.py tests\test_phase11_git.py tests\test_language.py tests\test_presentation.py tests\test_build_specs.py tests\test_release.py"
echo.
echo ---- Проверка набора задач бенчмарка ----
"%PY%" -m eval.agent_bench --validate
if errorlevel 1 (set "RES_bench=ЕСТЬ ОШИБКИ" & set "ANYFAIL=1") else (set "RES_bench=OK")
call :summary
goto :after

:: ── 2. Все тесты по группам ─────────────────────────────────────────────────
:groups
call :init_run groups
call :run_group agent   "Агент"                      "yaml, httpx, dotenv"    "tests\test_agent_regressions.py tests\test_agent_structured.py tests\test_agent_bench.py tests\test_phase7_agent.py tests\test_phase11_git.py tests\test_language.py tests\test_presentation.py tests\test_build_specs.py tests\test_release.py tests\test_phase3_router.py tests\test_eval_custom.py"
call :run_group core    "Ядро: бэкенды, RAG, веб, CLI" "yaml, httpx, dotenv, numpy" "tests\test_phase1_serving.py tests\test_phase12_multi_model.py tests\test_phase2_rag.py tests\test_phase4_web.py tests\test_phase15_lite.py tests\test_theme.py"
call :run_group server  "Сервер: API, авторизация, биллинг" "fastapi, sqlalchemy" "tests\test_server.py tests\test_server_security.py tests\test_auth.py tests\test_phase13_orgs.py tests\test_w2_projects.py tests\test_w3_sessions.py tests\test_w4_usage.py tests\test_w5_billing.py tests\test_w6_streaming.py"
call :run_group desktop "Десктоп-приложение"         "PySide6"                "tests\test_desktop_window.py tests\test_browser_app.py tests\test_phase14_onboarding.py tests\test_phase17.py tests\test_phase175.py tests\test_phase18.py tests\test_wizard_autoinstall.py tests\test_training_pipeline.py"
call :run_group license "Лицензирование"             "cryptography"           "tests\test_licensing.py"
call :run_group train   "Данные и дообучение"        "yaml"                   "tests\test_phase5_data.py tests\test_phase6_ft.py tests\test_phase13_marketplace.py tests\test_training_presets.py tests\test_website.py"
call :summary
goto :after

:: ── 3. Как в CI ─────────────────────────────────────────────────────────────
:ci
call :init_run ci
echo.
echo Все тесты одним прогоном. Если нет PySide6 или FastAPI, часть файлов
echo упадёт на импорте - для понятной сводки используйте пункт 2.
echo.
"%PY%" -m pytest tests -q -rfE -p no:cacheprovider --junitxml="%LOGDIR%\ci.xml"
if errorlevel 1 (set "RES_ci=ЕСТЬ ПАДЕНИЯ" & set "ANYFAIL=1") else (set "RES_ci=OK")
call :summary
goto :after

:: ── 4. Пробный прогон агента на модели ──────────────────────────────────────
:smoke
echo.
echo Агент решает 3 задачи на реальной модели: создать файл - сценарий t3,
echo исправить баг, найти значение в длинном файле. Модель грузится из
echo config\config.yaml; первая загрузка может занять время.
echo.
"%PY%" -m eval.agent_bench --tasks "create-calc,fix-off-by-one,qa-long-file" --modes json --out "results\agent_bench\smoke-%TS%"
if errorlevel 1 goto :bench_error
echo.
echo Отчёт: results\agent_bench\smoke-%TS%\report.md
goto :after

:: ── 5. Полный замер ─────────────────────────────────────────────────────────
:bench
echo.
echo Полный замер: 36 задач x 3 режима - baseline, text, json.
echo На CPU около 2,5 часа, каждая задача не дольше 15 минут.
echo Прогресс виден по шагам. Результаты сохраняются после каждой задачи:
echo если закрыть окно, повторный запуск этого пункта продолжит с места остановки.
if exist "results\agent_bench\full\results.jsonl" echo Найден незавершённый прогон - он будет продолжен.
if defined INTERACTIVE (
    choice /c YN /m "Запустить"
    if errorlevel 2 goto :menu
)
"%PY%" -m eval.agent_bench --modes "baseline,text,json" --repeats 1 --out "results\agent_bench\full"
if errorlevel 1 goto :bench_error
echo.
echo Отчёт: results\agent_bench\full\report.md
echo Для большей точности - ещё 2 повтора в ту же папку:
echo   "%PY%" -m eval.agent_bench --modes "baseline,text,json" --repeats 3 --out "results\agent_bench\full"
if defined INTERACTIVE start "" notepad "results\agent_bench\full\report.md"
goto :after

:bench_error
echo.
echo [ОШИБКА] Замер не выполнился. Частые причины:
echo   - не задана модель: проверьте backend и gguf_path в config\config.yaml
echo   - не установлен llama-cpp-python или не запущена Ollama
echo   - см. текст ошибки выше
set "ANYFAIL=1"
goto :after

:: ── 6. Последний отчёт ──────────────────────────────────────────────────────
:report
set "LATEST="
for /f "delims=" %%d in ('dir /b /ad /o-d "results\agent_bench" 2^>nul') do (
    if not defined LATEST if exist "results\agent_bench\%%d\report.md" set "LATEST=results\agent_bench\%%d\report.md"
)
if not defined LATEST (
    echo Отчётов пока нет. Запустите пункт 4 или 5.
    goto :after
)
echo Открываю %LATEST%
start "" notepad "%LATEST%"
goto :after

:: ── Завершение ──────────────────────────────────────────────────────────────
:after
if defined INTERACTIVE (
    echo.
    pause
    goto :menu
)
if defined ANYFAIL exit /b 1
exit /b 0

:: ============================================================================
::  Подпрограммы
:: ============================================================================

:init_run
set "ANYFAIL="
set "GROUPS="
set "RES_bench="
set "RES_ci="
set "LOGDIR=%LOGROOT%\%TS%-%~1"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
exit /b 0

:: call :run_group <ключ> "<название>" "<модули для проверки>" "<файлы тестов>"
:run_group
set "G_KEY=%~1"
set "RES_%G_KEY%="
set "TITLE_%G_KEY%=%~2"
set "GROUPS=%GROUPS% %G_KEY%"
"%PY%" -c "import %~3" >nul 2>&1
if errorlevel 1 (
    set "RES_%G_KEY%=ПРОПУЩЕНО: не установлены пакеты %~3"
    exit /b 0
)
echo.
echo ---- %~2 ----
"%PY%" -m pytest -q -rfE -p no:cacheprovider --junitxml="%LOGDIR%\%G_KEY%.xml" %~4
set "RC=%errorlevel%"
if "%RC%"=="0" set "RES_%G_KEY%=OK"
if "%RC%"=="1" set "RES_%G_KEY%=ЕСТЬ ПАДЕНИЯ - см. вывод выше"
if "%RC%"=="5" set "RES_%G_KEY%=тесты не найдены"
if "%RC%"=="2" set "RES_%G_KEY%=ОШИБКА ИМПОРТА при сборке тестов - не хватает пакета, см. вывод выше"
if "%RC%"=="1" set "ANYFAIL=1"
if "%RC%"=="2" set "ANYFAIL=1"
if not "%RC%"=="0" if not "%RC%"=="1" if not "%RC%"=="2" if not "%RC%"=="5" (
    set "RES_%G_KEY%=ОШИБКА ЗАПУСКА pytest, код %RC%"
    set "ANYFAIL=1"
)
exit /b 0

:summary
echo.
echo ============================================================
echo    ИТОГ
echo ============================================================
for %%g in (%GROUPS%) do call :print_result %%g
if defined RES_bench echo   Набор задач бенчмарка: %RES_bench%
if defined RES_ci echo   Все тесты: %RES_ci%
echo.
if defined ANYFAIL (
    echo   Есть падения. Подробности - в выводе выше.
) else (
    echo   Всё, что удалось запустить, прошло.
)
echo   Отчёты JUnit: %LOGDIR%
exit /b 0

:print_result
call echo   %%TITLE_%1%%: %%RES_%1%%
exit /b 0
