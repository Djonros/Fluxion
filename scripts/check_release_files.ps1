# Release guard: build output and huge files must not reach GitHub.
#
# Checks what fluxion-release.bat is about to push: files in commits that are
# not on GitHub yet (not on any remote branch) and files staged for the
# release commit.  BaseRef is accepted for compatibility and not needed.
# GitHub rejects files over 100 MB, and build output (dist, its copies such as
# "dist — копия", exe/dll) does not belong in the repository at all.
# Exit code 1 lists the offending files.
param([string]$BaseRef = "")

$ErrorActionPreference = "Stop"
# Paths with Cyrillic: read git's output as UTF-8, and pipe text between git
# commands as UTF-8 without BOM (PowerShell 5.1 pipes ASCII by default).
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

$LimitBytes = 50MB          # GitHub warns at 50 MB and refuses over 100 MB
$BadExtensions = @(".exe", ".dll", ".pyd", ".pak", ".7z", ".gguf", ".msi", ".safetensors")

function Test-BadPath([string]$path) {
    $lower = $path.ToLowerInvariant()
    if ($lower -match '^(dist|build)[^/]*/') { return $true }          # dist/, "dist — копия"/, build_x/
    if ($lower -match '(^|/)[^/]*(копия|- copy)[^/]*/') { return $true } # Windows folder copies
    return $BadExtensions -contains [IO.Path]::GetExtension($lower)
}

$found = New-Object System.Collections.Generic.List[string]

# 1) files in commits that GitHub has not received yet: everything reachable
#    from HEAD that no remote-tracking branch has.  Unlike "$BaseRef..HEAD"
#    this needs no particular branch name and also catches a bad commit
#    buried under newer ones.
$lines = & git rev-list --objects HEAD --not --remotes | & git cat-file "--batch-check=%(objecttype) %(objectsize) %(rest)"
if ($true) {
    foreach ($line in $lines) {
        $parts = $line -split " ", 3
        if ($parts.Count -lt 3 -or $parts[0] -ne "blob") { continue }
        $size = [int64]$parts[1]
        $path = $parts[2]
        if ($size -gt $LimitBytes -or (Test-BadPath $path)) {
            $found.Add(("{0}  ({1} МБ, в неотправленном коммите)" -f $path, [math]::Round($size / 1MB, 1)))
        }
    }
}

# 2) files staged for the release commit (added or modified)
$staged = (& git -c core.quotepath=false diff --cached --name-only -z --diff-filter=AM) -split [char]0 | Where-Object { $_ }
foreach ($path in $staged) {
    $full = Join-Path (Get-Location) $path
    $size = if (Test-Path -LiteralPath $full) { (Get-Item -LiteralPath $full).Length } else { 0 }
    if ($size -gt $LimitBytes -or (Test-BadPath $path)) {
        $found.Add(("{0}  ({1} МБ)" -f $path, [math]::Round($size / 1MB, 1)))
    }
}

if ($found.Count -eq 0) {
    Write-Host "      Проверка файлов выпуска: OK (нет результатов сборки и файлов больше 50 МБ)"
    exit 0
}

$unique = $found | Select-Object -Unique
Write-Host ""
Write-Host "В выпуск попали файлы сборки или слишком большие файлы:" -ForegroundColor Yellow
$unique | Select-Object -First 15 | ForEach-Object { Write-Host ("  " + $_) }
if ($unique.Count -gt 15) { Write-Host ("  ... и ещё " + ($unique.Count - 15)) }
exit 1
