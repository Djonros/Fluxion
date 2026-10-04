# Build the Fluxion Browser LITE artifact (Phase 15).
#   1) PyInstaller via fluxion-desktop-browser-lite.spec
#   2) trim debug/devtools paks + non-ru translations
#   3) pack dist\FluxionBrowserLite into dist\artifacts\*.7z (< 200 MB target)
# Usage: powershell -File scripts\build_lite.ps1 [-SkipBuild] [-NoPack]
#   A running copy from dist\FluxionBrowserLite is closed and a folder held by
#   Explorer/a terminal is worked around (build to dist\_build_tmp, then move).
#   -NoPack          build the app folder only, skip the 7z archive (no 7-Zip needed)
#   $env:FLUXION_PYTHON  interpreter to build with (default: python from PATH);
#                    it must have the app dependencies, PySide6 and llama-cpp-python
param([switch]$SkipBuild, [switch]$NoPack)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$appDir = Join-Path $root "dist\FluxionBrowserLite"
$artifactDir = Join-Path $root "dist\artifacts"
$py = if ($env:FLUXION_PYTHON) { $env:FLUXION_PYTHON } else { "python" }

# --- 0) free the output folder ---
# PyInstaller deletes the previous build first and fails with "WinError 32"
# when something holds it: the app (or its QtWebEngineProcess) still running
# from dist, an Explorer window or a terminal opened in that folder, or an
# antivirus scan in progress.
function Stop-DistProcesses([string]$dir) {
    if (-not (Test-Path -LiteralPath $dir)) { return }
    $prefix = (Resolve-Path -LiteralPath $dir).Path.TrimEnd('\') + '\'
    $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
    })
    if ($procs.Count -gt 0) {
        $names = ($procs | ForEach-Object { "$($_.ProcessName) ($($_.Id))" }) -join ", "
        Write-Host "[lite] закрываю программу, запущенную из папки сборки: $names" -ForegroundColor Yellow
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

# $true - the folder is gone; $false - only an empty folder is left that
# something holds (Explorer window, terminal): build elsewhere and move in.
function Clear-OutputDir([string]$dir) {
    for ($i = 1; $i -le 5; $i++) {
        if (-not (Test-Path -LiteralPath $dir)) { return $true }
        try {
            Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction Stop
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    $left = @(Get-ChildItem -LiteralPath $dir -Recurse -Force -File -ErrorAction SilentlyContinue)
    if ($left.Count -gt 0) {
        throw ("Не удаётся удалить старую сборку: занят файл $($left[0].FullName). " +
               "Закройте Fluxion (и QtWebEngineProcess.exe в Диспетчере задач), окна Проводника " +
               "и терминалы, открытые в папке dist, затем повторите сборку.")
    }
    return $false
}

if (-not $SkipBuild) {
    Stop-DistProcesses $appDir
    $freed = Clear-OutputDir $appDir
    $distPath = Join-Path $root "dist"
    if (-not $freed) {
        Write-Host "[lite] папку $appDir удерживает окно Проводника или терминал - собираю во временную папку" -ForegroundColor Yellow
        $distPath = Join-Path $root "dist\_build_tmp"
        if (Test-Path -LiteralPath $distPath) { Remove-Item -LiteralPath $distPath -Recurse -Force }
    }

    Write-Host "[lite] PyInstaller build..." -ForegroundColor Cyan
    & $py -m PyInstaller fluxion-desktop-browser-lite.spec --noconfirm --distpath $distPath
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with code $LASTEXITCODE" }

    if (-not $freed) {
        $built = Join-Path $distPath "FluxionBrowserLite"
        # moving files INTO a held folder works; deleting the folder does not
        robocopy $built $appDir /E /MOVE /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Не удалось перенести сборку в $appDir (robocopy $LASTEXITCODE)" }
        Remove-Item -LiteralPath $distPath -Recurse -Force -ErrorAction SilentlyContinue
        $global:LASTEXITCODE = 0
    }
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
if ($NoPack) {
    Write-Host "[lite] -NoPack: app folder ready, archive skipped: $appDir" -ForegroundColor Green
    exit 0
}
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
