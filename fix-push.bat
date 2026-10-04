@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions
cd /d "%~dp0"
:: One-time cleanup after a push rejected for "dist — копия" (GH001).
:: Undoes the commits that never reached GitHub; files on disk are untouched.
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto :not_repo
echo Загрузка состояния с GitHub...
git fetch -q origin
if errorlevel 1 echo [!] Нет связи с GitHub - использую состояние, сохранённое при прошлой отправке.
git rev-parse -q --verify origin/main >nul 2>&1
if errorlevel 1 goto :no_base
echo Коммиты, которые не попали на GitHub и будут отменены:
git log --oneline origin/main..HEAD
choice /c YN /m "Отменить их (файлы на диске останутся)"
if errorlevel 2 exit /b 0
git reset -q --soft origin/main
git rm -r -q --cached --ignore-unmatch "dist — копия"
git tag -d v0.12.1 >nul 2>&1
echo.
echo Готово. Теперь уберите папку «dist — копия» из папки проекта
echo (удалите или перенесите) и запустите fluxion-release.bat.
pause
exit /b 0
:not_repo
echo [ОШИБКА] Запустите батник в корневой папке Fluxion.
pause
exit /b 1
:no_base
echo [ОШИБКА] Нет сохранённого состояния GitHub (origin/main) и нет связи,
echo          чтобы его загрузить. Повторите, когда соединение восстановится.
pause
exit /b 1
