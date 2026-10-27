# Build the Fluxion installer: dist\artifacts\FluxionSetup-<version>.exe (+ .sha256).
#   Runs after build_lite.ps1: packs dist\FluxionBrowserLite with Inno Setup
#   (installer\fluxion.iss). The version comes from desktop_browser\__init__.py.
# Usage: powershell -File scripts\build_installer.ps1 [-AppDir <folder>] [-Required]
#   Without Inno Setup the step is skipped (exit 0) with a hint how to install
#   it; -Required makes that an error.
#   $env:ISCC   full path to ISCC.exe when it is somewhere unusual
param([string]$AppDir = "", [switch]$Required)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $AppDir) { $AppDir = Join-Path $root "dist\FluxionBrowserLite" }
$artifactDir = Join-Path $root "dist\artifacts"
$script = Join-Path $root "installer\fluxion.iss"

function Find-Iscc {
    if ($env:ISCC -and (Test-Path -LiteralPath $env:ISCC)) { return $env:ISCC }
    $onPath = (Get-Command iscc -ErrorAction SilentlyContinue).Source
    if ($onPath) { return $onPath }
    $bases = @((Join-Path $env:LOCALAPPDATA "Programs"), ${env:ProgramFiles(x86)}, $env:ProgramFiles) | Where-Object { $_ }
    foreach ($base in $bases) {
        foreach ($name in "Inno Setup 6", "Inno Setup 7") {
            $candidate = Join-Path $base "$name\ISCC.exe"
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) {
    Write-Host "[setup] Inno Setup не найден - установщик FluxionSetup.exe не собран." -ForegroundColor Yellow
    Write-Host "        Установить (без прав администратора):"
    Write-Host "          winget install JRSoftware.InnoSetup --scope user"
    Write-Host "        или скачайте с https://jrsoftware.org/isdl.php, затем повторите сборку."
    if ($Required) { exit 1 }
    exit 0
}

$exe = Join-Path $AppDir "FluxionBrowserLite.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Not found: $exe (run build_lite.ps1 first)" }

$init = Get-Content -Raw -Encoding UTF8 (Join-Path $root "desktop_browser\__init__.py")
if ($init -notmatch 'APP_VERSION\s*=\s*"([0-9]+(\.[0-9]+){1,3})"') { throw "APP_VERSION not found in desktop_browser\__init__.py" }
$version = $Matches[1]

New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$setup = Join-Path $artifactDir "FluxionSetup-$version.exe"
if (Test-Path -LiteralPath $setup) { Remove-Item -LiteralPath $setup -Force }

Write-Host "[setup] Inno Setup: $iscc" -ForegroundColor Cyan
Write-Host "[setup] building FluxionSetup-$version.exe (a few minutes)..." -ForegroundColor Cyan
& $iscc /Qp "/DAppVersion=$version" "/DSourceDir=$AppDir" "/DOutputDir=$artifactDir" $script
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $setup)) { throw "ISCC finished but $setup is missing" }

# checksum next to the installer, in the sha256sum format: "<hash> *<file>"
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
$shaFile = "$setup.sha256"
[IO.File]::WriteAllText($shaFile, "$hash *FluxionSetup-$version.exe`n", (New-Object System.Text.UTF8Encoding $false))

$sizeMB = [math]::Round((Get-Item -LiteralPath $setup).Length / 1MB, 1)
Write-Host "[setup] installer: $setup ($sizeMB MB)" -ForegroundColor Green
Write-Host "[setup] SHA256:    $hash" -ForegroundColor Green
