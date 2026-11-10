# fluxion-version.bat 0.29.0: raise the version for a release.
#
# scripts\bump_version.py changes the version, the release date, the CHANGELOG
# («Unreleased» becomes the version) and puts a draft of RELEASE_NOTES_RU.md.
# Then: the diff, «Release vX.Y.Z» commit if you want, the tests and the build
# if you want. No tag and no GitHub release here: that is fluxion-release.bat.
param([string]$Version = "")

# native git writes warnings to stderr: exit codes decide, not the error stream
$ErrorActionPreference = "Continue"
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
[Console]::InputEncoding = $utf8
$OutputEncoding = $utf8
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail([string]$text) {
    Write-Host ""
    Write-Host "[ОШИБКА] $text" -ForegroundColor Red
    exit 1
}

function Ask([string]$question) {
    return ((Read-Host $question) -match '^\s*(д|да|y|yes)\s*$')
}

$py = "python"
foreach ($candidate in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")) {
    if (Test-Path $candidate) { $py = (Resolve-Path $candidate).Path; break }
}
$env:PYTHONUTF8 = "1"

if ($Version.Trim() -eq "") {
    $current = & $py -c "import desktop_browser as d; print(d.APP_VERSION)"
    $Version = Read-Host "Текущая версия $current. Новая версия (например 0.29.0)"
}

$files = @("desktop_browser/__init__.py", "licensing/release.py", "CHANGELOG.md", "RELEASE_NOTES_RU.md")
& git diff --ignore-cr-at-eol --quiet -- $files
if ($LASTEXITCODE -ne 0) {
    Fail "В файлах версии уже есть незакоммиченные правки ($($files -join ', ')). Закоммитьте их сначала (fluxion-commit.bat)."
}

& $py scripts\bump_version.py $Version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
& git --no-pager diff --ignore-cr-at-eol --stat -- $files
& git --no-pager diff --ignore-cr-at-eol -- desktop_browser/__init__.py licensing/release.py
Write-Host ""
Write-Host "RELEASE_NOTES_RU.md — заготовка: перепишите пункты для пользователей перед выпуском." -ForegroundColor Yellow
if (Ask "Открыть RELEASE_NOTES_RU.md в Блокноте сейчас? (д/н)") {
    Start-Process notepad.exe -ArgumentList (Join-Path $root "RELEASE_NOTES_RU.md") -Wait
}

if (Ask "Закоммитить «Release v$Version»? (д/н)") {
    & git add -- $files
    & git commit -q -m "Release v$Version" -m "See CHANGELOG.md and RELEASE_NOTES_RU.md"
    if ($LASTEXITCODE -ne 0) { Fail "git commit не прошёл." }
    $sha = (& git rev-parse --short HEAD)
    Write-Host "Закоммичено: $sha — Release v$Version (не отправлено; отправить — fluxion-commit.bat или git push origin HEAD:main)" -ForegroundColor Green
} else {
    Write-Host "Не закоммичено: правки остались в файлах."
}

if (Ask "Запустить тесты (fluxion-test.bat ci)? (д/н)") {
    & cmd /c "`"$root\fluxion-test.bat`" ci"
    if ($LASTEXITCODE -ne 0) { Fail "Тесты не прошли — сборку не начинаю." }
}
if (Ask "Собрать exe и установщик (fluxion-build.bat --lite)? (д/н)") {
    & cmd /c "`"$root\fluxion-build.bat`" --lite"
}
Write-Host ""
Write-Host "Дальше: выпуск на GitHub (тег и релиз со сборками) — fluxion-release.bat."
exit 0
