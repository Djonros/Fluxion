# fluxion-commit.bat: commit what you choose, then push to main if you want.
#
#   fluxion-commit.bat                  - asks for the message
#   fluxion-commit.bat "Сообщение"      - the message given
#
# The list skips files where only the line endings changed (CRLF/LF), as
# `git diff --ignore-cr-at-eol` does; .gitignore is respected. You pick all of
# them or some by number. The push is a plain `git push origin HEAD:main`:
# when GitHub has newer commits, yours go on top of them (rebase); a conflict
# stops everything and nothing is overwritten.
param([string]$Message = "")

# native git writes warnings to stderr: exit codes decide, not the error stream
$ErrorActionPreference = "Continue"
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
[Console]::InputEncoding = $utf8
$OutputEncoding = $utf8
Set-Location (Split-Path -Parent $PSScriptRoot)

function Fail([string]$text) {
    Write-Host ""
    Write-Host "[ОШИБКА] $text" -ForegroundColor Red
    exit 1
}

function Invoke-Git([string[]]$GitArgs) {
    $out = & git @GitArgs 2>&1
    return @{ Code = $LASTEXITCODE; Out = ($out | Out-String).Trim() }
}

function Ask([string]$question) {
    $answer = Read-Host $question
    return ($answer -match '^\s*(д|да|y|yes)\s*$')
}

if ((Invoke-Git @("rev-parse", "--is-inside-work-tree")).Code -ne 0) { Fail "Эта папка — не git-репозиторий." }
$branch = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD")).Out

# ── what changed: status -z keeps Cyrillic names whole ───────────────────────
$raw = & git -c core.quotepath=off status --porcelain=v1 -z --untracked-files=all
$entries = @()
$parts = ($raw -join "") -split "`0"
for ($i = 0; $i -lt $parts.Count; $i++) {
    $item = $parts[$i]
    if ($item.Length -lt 4) { continue }
    $code = $item.Substring(0, 2)
    $path = $item.Substring(3)
    if ($code[0] -eq 'R' -or $code[0] -eq 'C') { $i++ }  # a rename: the old name follows
    if ($code -ne "??" -and $code -notmatch 'D') {
        # only the line endings changed: not a change worth a commit
        & git diff HEAD --ignore-cr-at-eol --quiet -- "$path" 2>$null
        if ($LASTEXITCODE -eq 0) { continue }
    }
    $kind = switch -regex ($code) { '\?\?' { "новый" } 'D' { "удалён" } 'R' { "переименован" } default { "изменён" } }
    $entries += [pscustomobject]@{ Path = $path; Kind = $kind }
}

if ($entries.Count -eq 0) {
    Write-Host "Изменений нет (кроме окончаний строк) — коммитить нечего."
    exit 0
}

Write-Host "Ветка: $branch"
Write-Host "Изменения:"
for ($n = 0; $n -lt $entries.Count; $n++) {
    Write-Host ("  {0,3}. [{1}] {2}" -f ($n + 1), $entries[$n].Kind, $entries[$n].Path)
}
Write-Host ""
$pick = Read-Host "Что коммитить: Enter — все; номера через пробел или запятую (например 1 3 5-7); 0 — отмена"
$chosen = @()
if ($pick.Trim() -eq "") {
    $chosen = @($entries)
} elseif ($pick.Trim() -eq "0") {
    Write-Host "Отменено."
    exit 0
} else {
    foreach ($token in ($pick -split '[\s,;]+' | Where-Object { $_ })) {
        if ($token -match '^(\d+)-(\d+)$') {
            foreach ($k in [int]$Matches[1]..[int]$Matches[2]) { if ($k -ge 1 -and $k -le $entries.Count) { $chosen += $entries[$k - 1] } }
        } elseif ($token -match '^\d+$' -and [int]$token -ge 1 -and [int]$token -le $entries.Count) {
            $chosen += $entries[[int]$token - 1]
        } else {
            Fail "Не понял «$token»: нужны номера из списка."
        }
    }
    $chosen = @($chosen | Sort-Object Path -Unique)
}
if (-not $chosen) { Fail "Ничего не выбрано." }

if ($Message.Trim() -eq "") { $Message = Read-Host "Описание коммита" }
if ($Message.Trim() -eq "") { Fail "Пустое описание — коммит не сделан." }

# ── commit ──────────────────────────────────────────────────────────────────
foreach ($e in $chosen) {
    & git add --all -- "$($e.Path)"
    if ($LASTEXITCODE -ne 0) { Fail "git add не принял $($e.Path)" }
}
$guard = Join-Path $PSScriptRoot "check_release_files.ps1"
if (Test-Path $guard) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $guard
    if ($LASTEXITCODE -ne 0) {
        & git reset -q
        Fail "В коммит попали сборки или слишком большие файлы (список выше). Ничего не закоммичено."
    }
}
$msgFile = [IO.Path]::GetTempFileName()
[IO.File]::WriteAllText($msgFile, $Message.Trim() + "`n", $utf8)
& git commit -q -F $msgFile
$code = $LASTEXITCODE
Remove-Item $msgFile -ErrorAction SilentlyContinue
if ($code -ne 0) { Fail "git commit не прошёл." }
$sha = (Invoke-Git @("rev-parse", "--short", "HEAD")).Out
Write-Host ""
Write-Host "Закоммичено: $sha — $($chosen.Count) файл(ов)." -ForegroundColor Green

# ── push ────────────────────────────────────────────────────────────────────
if (-not (Ask "Запушить в main? (д/н)")) {
    Write-Host "Не отправлено: коммит остался на компьютере. Отправить позже — git push origin HEAD:main."
    exit 0
}
Write-Host "Загружаю состояние GitHub..."
if ((Invoke-Git @("fetch", "-q", "origin", "main")).Code -ne 0) { Fail "Нет связи с GitHub: коммит остался на компьютере, отправьте позже." }
& git merge-base --is-ancestor origin/main HEAD
if ($LASTEXITCODE -ne 0) {
    Write-Host "На GitHub есть новые коммиты — ставлю ваш поверх них..."
    $rebase = Invoke-Git @("rebase", "--autostash", "origin/main")  # other uncommitted edits wait aside
    if ($rebase.Code -ne 0) {
        & git rebase --abort 2>$null
        Write-Host $rebase.Out
        Fail ("Ваши правки пересекаются с новыми на GitHub (конфликт). Ничего не отправлено и не перезаписано: " +
              "коммит $sha остался как был. Разберите конфликт (git pull --rebase origin main) или попросите помочь.")
    }
}
$push = Invoke-Git @("push", "origin", "HEAD:main")
if ($push.Code -ne 0) {
    Write-Host $push.Out
    Fail "GitHub не принял отправку. Коммит остался на компьютере."
}
$sha = (Invoke-Git @("rev-parse", "HEAD")).Out
$url = (Invoke-Git @("remote", "get-url", "origin")).Out -replace '\.git$', '' -replace '^git@github\.com:', 'https://github.com/'
Write-Host "Отправлено в main: $($sha.Substring(0, 7))" -ForegroundColor Green
Write-Host "Проверки на GitHub: $url/commit/$sha/checks"
exit 0
