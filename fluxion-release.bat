@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
title Fluxion - выпуск версии
cd /d "%~dp0"

:: ============================================================================
::  Выпуск новой версии Fluxion на GitHub
::
::    fluxion-release.bat           - выпуск: проверки, тесты, коммит, тег,
::                                    отправка на GitHub, релиз со сборками
::    fluxion-release.bat --check   - только проверки и тесты, ничего не меняет
::
::  Версия берётся из desktop_browser\__init__.py (APP_VERSION), тег - v<версия>.
::  Перед выпуском: пересоберите exe (fluxion-update.bat --build-only) и
::  добавьте раздел версии в CHANGELOG.md.
:: ============================================================================

set "CHECK_ONLY="
if /i "%~1"=="--check" set "CHECK_ONLY=1"
set "TMPF=%TEMP%\fluxion-release-%RANDOM%"

:: ── Python и git ────────────────────────────────────────────────────────────
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY set "PY=python"
"%PY%" --version >nul 2>&1
if errorlevel 1 goto :no_python
git --version >nul 2>&1
if errorlevel 1 goto :no_git

"%PY%" -c "import desktop_browser as d; print(d.APP_VERSION)" > "%TMPF%.ver"
if errorlevel 1 goto :no_version
set /p VER=<"%TMPF%.ver"
set "TAG=v%VER%"

echo ============================================================
echo    FLUXION - выпуск версии %VER%
echo ============================================================
echo.

:: ── 1. Репозиторий ──────────────────────────────────────────────────────────
echo [1/7] Репозиторий...
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto :no_repo
:repo_ready
git remote get-url origin > "%TMPF%.url" 2>nul
if errorlevel 1 goto :no_remote
set /p REMOTE_URL=<"%TMPF%.url"
git rev-parse --abbrev-ref HEAD > "%TMPF%.br"
set /p BRANCH=<"%TMPF%.br"
echo       Репозиторий: %REMOTE_URL%
echo       Ветка:       %BRANCH%

:: ── 2. Тег ──────────────────────────────────────────────────────────────────
echo [2/7] Тег %TAG%...
git rev-parse -q --verify "refs/tags/%TAG%" >nul 2>&1
if not errorlevel 1 goto :tag_exists_local
git ls-remote --exit-code --tags origin "refs/tags/%TAG%" >nul 2>&1
if errorlevel 3 goto :remote_unreachable
if not errorlevel 2 goto :tag_exists_remote
echo       Свободен

:: ── 3. Тесты ────────────────────────────────────────────────────────────────
echo [3/7] Тесты...
"%PY%" -m pytest --version >nul 2>&1
if errorlevel 1 goto :no_pytest
set "PYTHONUTF8=1"
set "QT_QPA_PLATFORM=offscreen"
"%PY%" -m pytest -q -p no:cacheprovider tests\test_release.py tests\test_build_specs.py tests\test_agent_regressions.py tests\test_agent_structured.py tests\test_agent_bench.py tests\test_presentation.py tests\test_training_pipeline.py tests\test_training_presets.py tests\test_clean_machine.py tests\test_website.py tests\test_model_catalog_pro.py tests\test_model_download.py tests\test_license_key_file.py tests\test_search_optional.py tests\test_config_env.py tests\test_phase7_agent.py tests\test_phase11_git.py
if errorlevel 1 goto :tests_failed
"%PY%" -m eval.agent_bench --validate
if errorlevel 1 goto :tests_failed

if defined CHECK_ONLY goto :check_done

:: ── 4. Изменения ────────────────────────────────────────────────────────────
echo.
echo [4/7] Изменения, которые войдут в выпуск:
git status --short
set "KEEP_CONFIG="
git diff --quiet HEAD -- config/config.yaml config/repos.yaml config/continue_config.json 2>nul
if not errorlevel 1 goto :config_ok
echo.
echo       В config изменены ваши локальные настройки. Обычно их НЕ публикуют:
echo       там могут быть пути к моделям на вашем диске.
choice /c YN /m "      Включить изменения config в выпуск"
if errorlevel 2 set "KEEP_CONFIG=1"
:config_ok
echo.
choice /c YN /m "Закоммитить, поставить тег %TAG% и отправить на GitHub"
if errorlevel 2 goto :cancelled

:: ── 5. Коммит и тег ─────────────────────────────────────────────────────────
echo.
echo [5/7] Коммит и тег...
git add -A
if defined KEEP_CONFIG git reset -q -- config/config.yaml config/repos.yaml config/continue_config.json

:: Guard: build output (dist, its copies like "dist — копия", exe/dll) and
:: files over 50 MB must not reach GitHub - it rejects files over 100 MB.
:: Also checks commits not pushed yet: a bad commit left by an earlier run.
echo       Проверка файлов выпуска...
git fetch -q origin 2>nul
set "BASE_REF="
git rev-parse -q --verify "origin/%BRANCH%" >nul 2>&1
if not errorlevel 1 set "BASE_REF=origin/%BRANCH%"
if not exist "%~dp0scripts\check_release_files.ps1" if exist "%~dp0check_release_files.ps1" (
    echo       Переношу check_release_files.ps1 из корня в папку scripts...
    move /y "%~dp0check_release_files.ps1" "%~dp0scripts\" >nul
    git add -A
    if defined KEEP_CONFIG git reset -q -- config/config.yaml config/repos.yaml config/continue_config.json
)
if not exist "%~dp0scripts\check_release_files.ps1" goto :no_guard
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\check_release_files.ps1" -BaseRef "%BASE_REF%"
if errorlevel 1 goto :bad_files
git diff --cached --quiet
if not errorlevel 1 goto :nothing_to_commit
git commit -q -m "Release %TAG%" -m "See CHANGELOG.md and RELEASE_NOTES_RU.md"
if errorlevel 1 goto :commit_failed
:nothing_to_commit
git tag -a "%TAG%" -m "Fluxion %VER%"
if errorlevel 1 goto :commit_failed
echo       Коммит и тег %TAG% созданы

:: ── 6. Отправка ─────────────────────────────────────────────────────────────
echo [6/7] Отправка на GitHub...
git push origin "%BRANCH%"
if errorlevel 1 goto :push_failed
git push origin "%TAG%"
if errorlevel 1 goto :push_failed
echo       Отправлено

:: ── 7. Релиз на GitHub ──────────────────────────────────────────────────────
echo [7/7] Релиз на GitHub...
powershell -NoProfile -Command "$d = Join-Path (Get-Location) 'dist\artifacts'; $limit = 2GB; $out = @(); foreach ($kind in 'Lite','Full') { $f = Get-ChildItem -LiteralPath $d -Filter ('FluxionBrowser-' + $kind + '-*.7z') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($f) { $mb = [math]::Round($f.Length / 1MB); if ($f.Length -lt $limit) { $out += $f.FullName; Write-Host ('      ' + $f.Name + '  ' + $mb + ' MB  ' + $f.LastWriteTime) } else { Write-Host ('      [!] ' + $f.Name + ' - ' + $mb + ' MB: больше лимита GitHub 2 ГБ, не прикрепляется') } } }; Set-Content -Encoding UTF8 -LiteralPath ($env:TMPF + '.assets') -Value $out"
set "HAVE_ASSETS="
if exist "%TMPF%.assets" for %%A in ("%TMPF%.assets") do if %%~zA GTR 3 set "HAVE_ASSETS=1"
if not defined HAVE_ASSETS echo       [!] В dist\artifacts нет архивов сборки - релиз будет без файлов.
if defined HAVE_ASSETS echo       Убедитесь, что сборка сделана ПОСЛЕ обновления кода.

where gh >nul 2>&1
if errorlevel 1 goto :manual_release
gh auth status >nul 2>&1
if errorlevel 1 goto :gh_login

choice /c YN /m "      Создать релиз %TAG% на GitHub с этими файлами"
if errorlevel 2 goto :manual_release
powershell -NoProfile -Command "$files = @(Get-Content -Encoding UTF8 -LiteralPath ($env:TMPF + '.assets') | Where-Object { $_ }); & gh release create $env:TAG @files --title ('Fluxion ' + $env:VER) --notes-file 'RELEASE_NOTES_RU.md'; exit $LASTEXITCODE"
if errorlevel 1 goto :release_failed
goto :done

:: ── Итог ────────────────────────────────────────────────────────────────────
:done
del "%TMPF%.*" >nul 2>&1
echo.
echo ============================================================
echo    Версия %VER% выпущена
echo ============================================================
echo    Тег:     %TAG%
echo    Коммит и тег отправлены в %REMOTE_URL%
echo    Проверьте вкладку Actions на GitHub: CI прогонит тесты.
echo    Установленные программы увидят обновление при проверке обновлений.
echo.
pause
exit /b 0

:check_done
del "%TMPF%.*" >nul 2>&1
echo.
echo Проверка пройдена: версию %VER% можно выпускать.
echo Для выпуска запустите fluxion-release.bat без параметров.
echo.
pause
exit /b 0

:manual_release
del "%TMPF%.*" >nul 2>&1
echo.
echo Код и тег %TAG% уже на GitHub. Осталось оформить релиз на сайте:
echo   1. Откройте репозиторий на github.com - Releases - Draft a new release
echo   2. Выберите тег %TAG%, заголовок: Fluxion %VER%
echo   3. Текст: содержимое RELEASE_NOTES_RU.md
echo   4. Прикрепите файлы из dist\artifacts, перечисленные выше
echo   5. Publish release
echo Или установите GitHub CLI: winget install GitHub.cli
echo и выполните: gh auth login - тогда батник сделает это сам.
echo.
pause
exit /b 0

:gh_login
echo       GitHub CLI не авторизован. Выполните: gh auth login
goto :manual_release

:: ── Подключение папки к репозиторию ─────────────────────────────────────────
:no_repo
echo       Эта папка - не git-репозиторий: скорее всего, проект скачан архивом.
echo       Можно подключить её к репозиторию на GitHub. Файлы в папке не
echo       изменятся: git запомнит, чем они отличаются от версии на GitHub.
choice /c YN /m "      Подключить"
if errorlevel 2 goto :cancelled
set "REPO_URL=https://github.com/djonros/fluxion.git"
set /p "REPO_URL=      Адрес репозитория [%REPO_URL%]: "
git init -q
git remote add origin "%REPO_URL%"
echo       Загрузка истории с GitHub...
git fetch -q origin
if errorlevel 1 goto :fetch_failed
git rev-parse -q --verify origin/main >nul 2>&1
if not errorlevel 1 (set "BASE=main") else (set "BASE=master")
git rev-parse -q --verify "origin/%BASE%" >nul 2>&1
if errorlevel 1 goto :fetch_failed
git symbolic-ref HEAD "refs/heads/%BASE%"
git reset -q "origin/%BASE%"
git branch -q --set-upstream-to="origin/%BASE%" "%BASE%"
echo       Подключено к ветке %BASE%.
goto :repo_ready

:: ── Ошибки ──────────────────────────────────────────────────────────────────
:no_python
echo [ОШИБКА] Python не найден. Сначала запустите fluxion-setup.bat.
goto :fail
:no_git
echo [ОШИБКА] Git не найден. Установите Git for Windows: https://git-scm.com
goto :fail
:no_version
echo [ОШИБКА] Не удалось прочитать версию из desktop_browser\__init__.py.
goto :fail
:no_remote
echo [ОШИБКА] У репозитория нет адреса origin.
echo          Добавьте: git remote add origin https://github.com/ВЛАДЕЛЕЦ/fluxion.git
goto :fail
:fetch_failed
echo [ОШИБКА] Не удалось загрузить репозиторий: проверьте адрес и доступ к GitHub.
rmdir /s /q .git 2>nul
goto :fail
:tag_exists_local
echo [ОШИБКА] Тег %TAG% уже есть. Увеличьте APP_VERSION в
echo          desktop_browser\__init__.py и добавьте раздел в CHANGELOG.md.
goto :fail
:tag_exists_remote
echo [ОШИБКА] Версия %TAG% уже выпущена на GitHub. Увеличьте APP_VERSION.
goto :fail
:remote_unreachable
echo [ОШИБКА] Нет связи с GitHub или нет доступа к репозиторию.
goto :fail
:no_pytest
echo [ОШИБКА] pytest не установлен: "%PY%" -m pip install pytest
goto :fail
:tests_failed
echo.
echo [ОШИБКА] Проверки не прошли - выпуск остановлен, ничего не изменено.
goto :fail
:no_guard
git reset -q
echo [ОШИБКА] Не найден scripts\check_release_files.ps1 - без проверки файлов
echo          выпуск не делается. Положите файл в папку scripts внутри папки
echo          программы, рядом с build_lite.ps1, и запустите выпуск снова.
goto :fail

:bad_files
:: Undo commits that were not pushed (they hold the files) and unstage;
:: the working folder is untouched, nothing is lost.
if defined BASE_REF git reset -q --soft "%BASE_REF%"
git reset -q
if not defined BASE_REF echo          Отмените неотправленные коммиты вручную: git reset --soft origin/main
echo.
echo [ОШИБКА] Выпуск остановлен: GitHub такие файлы не примет, и в репозитории
echo          им не место. Неотправленный коммит отменён, файлы на диске не тронуты.
echo          Что сделать:
echo            - копию сборки, например папку «dist — копия», удалите или перенесите
echo              за пределы папки проекта; новые копии .gitignore уже исключает;
echo            - нужный крупный файл добавьте в .gitignore;
echo          затем запустите fluxion-release.bat снова.
goto :fail

:commit_failed
echo [ОШИБКА] Не удалось создать коммит или тег - см. вывод выше.
echo          Если git просит представиться, выполните один раз:
echo            git config --global user.name "Ваше имя"
echo            git config --global user.email "почта@example.com"
goto :fail
:push_failed
git tag -d "%TAG%" >nul 2>&1
echo.
echo [ОШИБКА] Отправка не удалась - см. вывод выше. Частые причины:
echo   - нужен вход в GitHub: Windows откроет окно авторизации при повторе;
echo   - на GitHub есть новые коммиты: выполните git pull --rebase и повторите.
echo Локальный тег удалён, коммит сохранён - после исправления просто
echo запустите fluxion-release.bat снова.
goto :fail
:release_failed
echo [ОШИБКА] Релиз не создан - см. вывод выше. Код и тег уже на GitHub:
echo          оформите релиз на сайте.
goto :manual_release

:cancelled
del "%TMPF%.*" >nul 2>&1
echo Отменено. Ничего не изменено.
pause
exit /b 0

:fail
del "%TMPF%.*" >nul 2>&1
echo.
pause
exit /b 1
