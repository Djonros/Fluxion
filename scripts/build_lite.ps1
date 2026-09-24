# Build the Fluxion Browser LITE artifact (Phase 15).
#   1) PyInstaller via fluxion-desktop-browser-lite.spec
#   2) trim debug/devtools paks + non-ru translations
#   3) pack dist\FluxionBrowserLite into dist\artifacts\*.7z (< 200 MB target)
# Usage: powershell -File scripts\build_lite.ps1 [-SkipBuild]
param([switch]$SkipBuild)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$appDir = Join-Path $root "dist\FluxionBrowserLite"
$artifactDir = Join-Path $root "dist\artifacts"

if (-not $SkipBuild) {
    Write-Host "[lite] PyInstaller build..." -ForegroundColor Cyan
    python -m PyInstaller fluxion-desktop-browser-lite.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with code $LASTEXITCODE" }
}

if (-not (Test-Path $appDir)) { throw "Not found: $appDir (build first)" }

function Get-DirMB([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    [math]::Round(((Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum / 1MB), 1)
}

$before = Get-DirMB $appDir
Write-Host "[lite] installed size before trim: $before MB"

# --- 2) trim: WebEngine debug/devtools resources ---
$pyside = Join-Path $appDir "_internal\PySide6"
$trimPatterns = @(
    "*.debug.pak",
    "qtwebengine_devtools_resources.pak",
    "v8_context_snapshot.debug.bin"
)
foreach ($pattern in $trimPatterns) {
    Get-ChildItem $pyside -Recurse -Filter $pattern -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Host "[lite] drop $($_.Name)"
            Remove-Item $_.FullName -Force
        }
}

# --- trim: keep only ru UI translations + en-US/ru webengine locales ---
$translations = Join-Path $pyside "translations"
if (Test-Path $translations) {
    Get-ChildItem $translations -Filter *.qm |
        Where-Object { $_.Name -notmatch "^(qtbase|qtwidgets|qtgui|qtnetwork)_ru\.qm$" } |
        ForEach-Object { Remove-Item $_.FullName -Force }
    $locales = Join-Path $translations "qtwebengine_locales"
    if (Test-Path $locales) {
        Get-ChildItem $locales -Filter *.qm |
            Where-Object { $_.BaseName -notin @("en-US", "ru") } |
            ForEach-Object { Remove-Item $_.FullName -Force }
    }
}

$after = Get-DirMB $appDir
Write-Host "[lite] installed size after trim: $after MB (saved $([math]::Round($before - $after, 1)) MB)" -ForegroundColor Green

# --- 3) pack ---
$sevenZip = (Get-Command 7z -ErrorAction SilentlyContinue).Source
if (-not $sevenZip) {
    $candidate = "C:\Program Files\7-Zip\7z.exe"
    if (Test-Path $candidate) { $sevenZip = $candidate }
}
if (-not $sevenZip) { throw "7z not found (install 7-Zip or add to PATH)" }

New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
$artifact = Join-Path $artifactDir "FluxionBrowser-Lite-win64-$stamp.7z"
if (Test-Path $artifact) { Remove-Item $artifact -Force }

Write-Host "[lite] packing 7z (mx=9)..." -ForegroundColor Cyan
& $sevenZip a -mx=9 $artifact "$appDir\*" -bso0 -bsp0
if ($LASTEXITCODE -ne 0) { throw "7z failed with code $LASTEXITCODE" }

$sizeMB = [math]::Round((Get-Item $artifact).Length / 1MB, 1)
$verdict = if ($sizeMB -lt 200) { "OK (< 200 MB)" } else { "OVER LIMIT" }
Write-Host "[lite] artifact: $artifact" -ForegroundColor Green
Write-Host "[lite] installer size: $sizeMB MB - $verdict" -ForegroundColor Green
