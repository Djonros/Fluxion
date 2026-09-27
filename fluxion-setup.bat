@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - установка
cd /d "%~dp0"

:: ============================================================================
::  Установка Fluxion для запуска из исходников (Windows)
::
::    1. Python и виртуальное окружение .venv (создаётся по желанию)
::    2. зависимости приложения (requirements.txt)
::    3. встроенный движок llama.cpp - модели работают без Ollama
::    4. по желанию: API-сервер для расширения VS Code
::    5. проверка
::
::  Модель скачивается в самом приложении: страница «Модели».
::  Батник можно запускать повторно - установленное пропускается.
:: ============================================================================

echo ============================================================
echo    FLUXION - установка
echo ============================================================
echo.

:: ── 1. Python ───────────────────────────────────────────────────────────────
echo [1/5] Python...
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if defined PY goto :py_ready

python --version >nul 2>&1
if errorlevel 1 goto :no_python
echo       Виртуальное окружение не найдено. Рекомендуется создать его,
echo       чтобы зависимости Fluxion не смешивались с другими программами.
choice /c YN /m "      Создать .venv в папке программы"
if errorlevel 2 goto :use_system_python
python -m venv .venv
if errorlevel 1 goto :venv_failed
set "PY=%~dp0.venv\Scripts\python.exe"
goto :py_ready

:use_system_python
set "PY=python"

:py_ready
"%PY%" --version
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto :old_python

:: ── 2. Зависимости ──────────────────────────────────────────────────────────
echo.
echo [2/5] Зависимости приложения - при первом запуске несколько минут...
"%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%PY%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto :deps_failed
echo       Готово

:: ── 3. Встроенный движок llama.cpp ──────────────────────────────────────────
echo.
echo [3/5] Движок llama.cpp...
"%PY%" -c "import llama_cpp" >nul 2>&1
if not errorlevel 1 goto :engine_ready

echo       Пробую готовую сборку для процессора...
"%PY%" -m pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --disable-pip-version-check
"%PY%" -c "import llama_cpp" >nul 2>&1
if not errorlevel 1 goto :engine_ready

echo.
echo       Готовой сборки для этой версии Python нет. Движок можно собрать
echo       из исходников: нужен Visual Studio 2022 с компонентом C++,
echo       сборка занимает 10-15 минут.
echo         1. Собрать для процессора
echo         2. Установить версию для видеокарты NVIDIA - нужен CUDA 12.4
echo         3. Пропустить - тогда нужна Ollama или облачный API
set "FLUXION_PYTHON=%PY%"
choice /c 123 /n /m "      Выберите: "
if errorlevel 3 goto :engine_skipped
if errorlevel 2 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_llamacpp.ps1" -Gpu
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_llamacpp.ps1"
)
"%PY%" -c "import llama_cpp" >nul 2>&1
if errorlevel 1 goto :engine_failed

:engine_ready
echo       Движок llama.cpp - OK
goto :server_step

:engine_skipped
echo       Пропущено.
goto :server_step

:engine_failed
echo       [!] Движок не установился - причина в выводе выше.
echo           Повторить: запустите fluxion-setup.bat ещё раз.

:: ── 4. Сервер ───────────────────────────────────────────────────────────────
:server_step
echo.
echo [4/5] API-сервер нужен только для расширения VS Code.
"%PY%" -c "import fastapi, sqlalchemy" >nul 2>&1
if not errorlevel 1 goto :server_ready
choice /c YN /m "      Установить зависимости сервера"
if errorlevel 2 goto :verify
"%PY%" -m pip install -r server\requirements.txt --disable-pip-version-check
if errorlevel 1 echo       [!] Зависимости сервера не установились - см. вывод выше.
goto :verify
:server_ready
echo       Сервер - OK

:: ── 5. Проверка ─────────────────────────────────────────────────────────────
:verify
echo.
echo [5/5] Проверка...
"%PY%" -c "from core.config import Settings; from core.backend_factory import _detect_backend_type; s = Settings.load(); print('      Конфиг загружен, движок:', _detect_backend_type(s))"
if errorlevel 1 goto :verify_failed

echo.
echo ============================================================
echo    Установка завершена
echo ============================================================
echo    Запуск программы:     fluxion-desktop.bat
echo    Модель:               в программе, страница «Модели»
echo    Проверка:             fluxion-test.bat, пункт 1
echo    CLI в терминале:      fluxion.bat
echo    Сервер для VS Code:   fluxion-server.bat
echo.
pause
exit /b 0

:: ── Ошибки ──────────────────────────────────────────────────────────────────
:no_python
echo.
echo [ОШИБКА] Python не найден.
echo          Установите Python 3.11 или новее: https://www.python.org/downloads/
echo          и отметьте в установщике пункт Add Python to PATH.
goto :fail

:old_python
echo.
echo [ОШИБКА] Нужен Python 3.11 или новее.
goto :fail

:venv_failed
echo.
echo [ОШИБКА] Не удалось создать виртуальное окружение .venv.
goto :fail

:deps_failed
echo.
echo [ОШИБКА] Зависимости не установились - причина в выводе выше.
goto :fail

:verify_failed
echo.
echo [ОШИБКА] Конфигурация не загрузилась - см. вывод выше.
goto :fail

:fail
echo.
pause
exit /b 1
