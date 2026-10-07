# Read-only audit of the repository before it is made public.
# Scans the WHOLE history (all branches and tags), not only the current files.
# Exit code: 0 - no blockers, 1 - blockers found, 2 - could not run.
# Usage: powershell -File scripts\check_public.ps1 [-LargeMB 10] [-Root <repo folder>]
param([int]$LargeMB = 10, [string]$Root = "")

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$root = if ($Root) { $Root } else { Split-Path -Parent $PSScriptRoot }
Set-Location -LiteralPath $root

git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ОШИБКА] Это не git-репозиторий: $root" -ForegroundColor Red
    exit 2
}

$script:blockers = 0
$script:warnings = 0
$script:report = New-Object System.Collections.Generic.List[string]

function Say([string]$text, [string]$color = "Gray") {
    Write-Host $text -ForegroundColor $color
    $script:report.Add($text)
}
function Section([string]$title) { Say ""; Say "== $title ==" "Cyan" }
function Blocker([string]$text) { $script:blockers++; Say "  [СТОП] $text" "Red" }
function Warn([string]$text) { $script:warnings++; Say "  [!] $text" "Yellow" }
function Ok([string]$text) { Say "  [ok] $text" "Green" }
function Info([string]$text) { Say "       $text" }

function GitLines {
    $out = & git -c core.quotepath=off @args 2>$null
    if ($null -eq $out) { return @() }
    return @($out | Where-Object { $_ -ne "" })
}

$commits = (GitLines rev-list --all --count)[0]
$refs = (GitLines for-each-ref "--format=%(refname:short)") -join ", "
Say "Проверка перед открытием репозитория: $root"
Say "Коммитов во всей истории: $commits. Ветки и теги: $refs"

$tracked = @{}
foreach ($f in (GitLines ls-files)) { $tracked[$f] = $true }
$everAdded = GitLines log --all "--pretty=format:" --name-only --no-renames --diff-filter=A | Sort-Object -Unique

function Where-Is([string]$path) {
    if ($tracked.ContainsKey($path)) { return "есть сейчас" }
    return "только в истории"
}

# ---------------------------------------------------------------------------
Section "1. Закрытые файлы в истории"
$blockPatterns = @(
    @{ re = '(^|/)keys/';                          why = "папка keys/ (закрытый ключ подписи лицензий)" },
    @{ re = '\.pem$';                              why = "файл ключа .pem" },
    @{ re = '(^|/)license\.key$';                  why = "активированная лицензия" },
    @{ re = '\.key$';                              why = "файл ключа .key" },
    @{ re = '(^|/)\.env(\.[^/]*)?$';               why = "файл окружения с секретами" },
    @{ re = '(^|/)data/';                          why = "папка data/ (лицензии, история чатов, журнал выданных ключей)" },
    @{ re = '(^|/)(issued|registry)[^/]*\.(json|jsonl|csv)$'; why = "журнал выданных ключей (почты покупателей)" },
    @{ re = '(^|/)(chats|chat_history|trial)\.json$'; why = "личные данные программы" },
    @{ re = '(^|/)id_(rsa|ed25519|ecdsa)';         why = "SSH-ключ" }
)
$allowed = '(^|/)\.env\.example$|(^|/)tests/'
$found = $false
foreach ($path in $everAdded) {
    if ($path -match $allowed) { continue }
    foreach ($p in $blockPatterns) {
        if ($path -match $p.re) {
            Blocker ("{0} - {1} ({2})" -f $path, $p.why, (Where-Is $path))
            $found = $true
            break
        }
    }
}
if (-not $found) { Ok "ключей, .env, data/ и keys/ в истории нет" }

# ---------------------------------------------------------------------------
Section "2. Секреты в тексте (вся история)"
$secretPatterns = @(
    @{ name = "закрытый ключ (PEM)";   re = 'BEGIN [A-Z ]*PRIVATE KEY';        block = $true },
    @{ name = "токен Hugging Face";    re = 'hf_[A-Za-z0-9]{30,}';             block = $true },
    @{ name = "токен GitHub";          re = 'gh[pousr]_[A-Za-z0-9]{30,}';      block = $true },
    @{ name = "токен GitHub (PAT)";    re = 'github_pat_[A-Za-z0-9_]{30,}';    block = $true },
    @{ name = "ключ OpenAI/Anthropic"; re = 'sk-[A-Za-z0-9_-]{32,}';           block = $true },
    @{ name = "ключ AWS";              re = 'AKIA[0-9A-Z]{16}';                block = $true },
    @{ name = "токен Slack";           re = 'xox[baprs]-[A-Za-z0-9-]{10,}';    block = $true },
    @{ name = "токен Telegram-бота";   re = '[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}'; block = $true },
    @{ name = "пароль или секрет в присваивании"; re = '(PASSWORD|PASSWD|SECRET|API_KEY|TOKEN)[A-Z_]*[[:space:]]*[=:][[:space:]]*["'']?[A-Za-z0-9+/_-]{16,}'; block = $false }
)
$found = $false
foreach ($p in $secretPatterns) {
    $hits = GitLines log --all --extended-regexp "-G$($p.re)" "--pretty=format:@%h %ad %s" --date=short --name-only
    if ($hits.Count -eq 0) { continue }
    $found = $true
    $files = @($hits | Where-Object { -not $_.StartsWith("@") } | Sort-Object -Unique)
    $commitsHit = @($hits | Where-Object { $_.StartsWith("@") })
    $text = "{0}: коммитов {1}, файлов {2}" -f $p.name, $commitsHit.Count, $files.Count
    $outsideTests = @($files | Where-Object { $_ -notmatch '^tests/' })
    if ($p.block -and $outsideTests.Count -gt 0) { Blocker $text }
    elseif ($p.block) { Warn "$text - только в тестах, похоже на заглушку" }
    else { Warn $text }
    foreach ($f in ($files | Select-Object -First 12)) { Info ("{0} ({1})" -f $f, (Where-Is $f)) }
    if ($files.Count -gt 12) { Info ("... и ещё {0}" -f ($files.Count - 12)) }
    Info ("первый коммит: " + $commitsHit[-1].Substring(1))
}
if (-not $found) { Ok "известных токенов, паролей и закрытых ключей в тексте нет" }
else { Info "Находку только в tests/ считаю заглушкой, но откройте файл и убедитесь сами." }

# ---------------------------------------------------------------------------
Section "3. Внутренние материалы, архивы и сборки"
$warnPatterns = @(
    @{ re = '(^|/)docs/internal/';                      why = "внутренние документы" },
    @{ re = '(^|/)OWNER_GUIDE';                         why = "руководство владельца (выдача ключей, цены?)" },
    @{ re = '\.(zip|7z|rar|tar|gz)$';                   why = "архив" },
    @{ re = '\.(exe|dll|pyd|msi|pak)$';                 why = "собранный файл" },
    @{ re = '\.(gguf|safetensors|bin)$';                why = "модель" },
    @{ re = '\.patch$';                                 why = "патч (может содержать старый код и пути)" },
    @{ re = '(копия|- Copy|backup|\.bak$|\.orig$)';     why = "копия или резерв" }
)
$found = $false
foreach ($path in $everAdded) {
    foreach ($p in $warnPatterns) {
        if ($path -match $p.re) {
            Warn ("{0} - {1} ({2})" -f $path, $p.why, (Where-Is $path))
            $found = $true
            break
        }
    }
}
if (-not $found) { Ok "внутренних документов, архивов и сборок в истории нет" }

# ---------------------------------------------------------------------------
Section "4. Большие файлы в истории (от $LargeMB МБ)"
$limit = $LargeMB * 1MB
$big = @()
$objects = & git -c core.quotepath=off rev-list --objects --all 2>$null |
    & git cat-file "--batch-check=%(objecttype) %(objectsize) %(rest)" 2>$null
foreach ($line in $objects) {
    $parts = $line -split ' ', 3
    if ($parts.Count -lt 3 -or $parts[0] -ne "blob") { continue }
    if ([long]$parts[1] -ge $limit) { $big += [pscustomobject]@{ Size = [long]$parts[1]; Path = $parts[2] } }
}
if ($big.Count -eq 0) { Ok "больших файлов нет" }
foreach ($b in ($big | Sort-Object Size -Descending | Select-Object -First 15)) {
    Warn ("{0:N1} МБ  {1} ({2})" -f ($b.Size / 1MB), $b.Path, (Where-Is $b.Path))
}

# ---------------------------------------------------------------------------
Section "5. Личные данные"
$authors = GitLines log --all "--format=%an <%ae>" | Sort-Object -Unique
Info "Авторы коммитов - станут видны всем:"
foreach ($a in $authors) { Info "  $a" }
if (@($authors | Where-Object { $_ -notmatch 'noreply' }).Count -gt 0) {
    Warn "в коммитах настоящие адреса почты; если это нежелательно, см. отчёт внизу"
}

$userPaths = GitLines grep -I -l -i -E 'C:[\\/]+Users[\\/]+[A-Za-z0-9._-]+' -- . |
    Where-Object { $_ -notmatch '^tests/' }
if ($userPaths.Count -gt 0) {
    Warn ("пути вида C:\Users\<имя> в {0} файлах:" -f $userPaths.Count)
    foreach ($f in ($userPaths | Select-Object -First 10)) { Info "  $f" }
} else { Ok "путей C:\Users\<имя> вне тестов нет" }

$emails = GitLines grep -I -o -h -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- . |
    Where-Object { $_ -notmatch 'example\.|noreply|@local$|\.png$|\.jpg$|@2x|pytest|users\.noreply' } |
    Sort-Object -Unique
if ($emails.Count -gt 0) {
    Info "Адреса почты в текущих файлах (проверьте, что все они публичные):"
    foreach ($e in ($emails | Select-Object -First 20)) { Info "  $e" }
}

# ---------------------------------------------------------------------------
Section "6. Готовность к публикации"
if ($tracked.ContainsKey("LICENSE")) { Ok "файл LICENSE есть" } else { Warn "нет файла LICENSE: без него код формально нельзя использовать" }
if ($tracked.ContainsKey("README.md")) { Ok "README.md есть" } else { Warn "нет README.md" }
$ignore = if (Test-Path -LiteralPath ".gitignore") { Get-Content -LiteralPath ".gitignore" } else { @() }
foreach ($need in @("keys/", "data/", ".env")) {
    if ($ignore -contains $need) { Ok ".gitignore закрывает $need" } else { Warn ".gitignore не закрывает $need" }
}
$dirty = GitLines status --porcelain
if ($dirty.Count -gt 0) { Info ("Незакоммиченных изменений: {0} (в проверку истории не входят)" -f $dirty.Count) }
$unpushed = GitLines log --branches --not --remotes --oneline
if ($unpushed.Count -gt 0) { Info ("Неотправленных коммитов: {0}" -f $unpushed.Count) }

# ---------------------------------------------------------------------------
Section "ИТОГ"
if ($script:blockers -gt 0) { Say ("  Стоп-пунктов: {0}. Открывать репозиторий НЕЛЬЗЯ." -f $script:blockers) "Red" }
else { Say "  Стоп-пунктов нет." "Green" }
Say ("  Предупреждений: {0}." -f $script:warnings) "Yellow"
Say ""
Say "  Что делать с находками:"
Say "   - Файл 'есть сейчас': git rm --cached <файл>, добавить в .gitignore, закоммитить."
Say "   - Файл 'только в истории' всё равно будет виден после открытия. Надёжный способ -"
Say "     открыть НОВЫЙ репозиторий с одним чистым коммитом текущего кода, а старый"
Say "     оставить закрытым. Переписывание истории (git filter-repo) сложнее и не"
Say "     убирает то, что уже скачано или лежит в форках."
Say "   - Найденный токен или пароль считается утёкшим: отзовите его и выпустите новый."
Say "   - Закрытый ключ подписи лицензий в истории = нужен новый ключ, старые лицензии"
Say "     придётся перевыпустить."
Say "  Чего эта проверка не видит: секреты GitHub Actions, тексты issues и pull"
Say "  request, файлы в Releases, вики. Просмотрите их на GitHub вручную."

$logDir = Join-Path $root "logs"
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("public-check-{0}.txt" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
[System.IO.File]::WriteAllLines($logFile, $script:report, (New-Object System.Text.UTF8Encoding($true)))
Write-Host ""
Write-Host "Отчёт сохранён: $logFile"

if ($script:blockers -gt 0) { exit 1 }
exit 0
