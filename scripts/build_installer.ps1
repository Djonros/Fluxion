# Build the Fluxion installer: dist\artifacts\FluxionSetup-<version>.exe (+ .sha256).
#   Runs after build_lite.ps1: packs dist\FluxionBrowserLite with Inno Setup
#   (installer\fluxion.iss). The version comes from desktop_browser\__init__.py.
# Usage: powershell -File scripts\build_installer.ps1 [-AppDir <folder>] [-Required]
#        powershell -File scripts\build_installer.ps1 -SignOnly <file>
#   Without Inno Setup the step is skipped (exit 0) with a hint how to install
#   it; -Required makes that an error.
#   $env:ISCC   full path to ISCC.exe when it is somewhere unusual
#
# Code signing (optional; without these variables nothing is signed):
#   $env:FLUXION_SIGN_THUMBPRINT  SHA1 thumbprint of a code signing certificate
#                     in the user's store or on a token: signtool signs with it
#   $env:FLUXION_SIGNTOOL_CMD     a signing command of your own (cloud signing:
#                     Certum SimplySign, SSL.com eSigner...), {file} = the file
#   $env:FLUXION_SIGN_TIMESTAMP   timestamp server (default DigiCert)
#   $env:SIGNTOOL     full path to signtool.exe (default: PATH or Windows Kits)
# FluxionBrowserLite.exe is signed before packing, the setup and its
# uninstaller by Inno Setup (SignTool), which calls this script with -SignOnly.
param([string]$AppDir = "", [switch]$Required, [string]$SignOnly = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Test-SigningEnabled {
    return [bool]($env:FLUXION_SIGN_THUMBPRINT -or $env:FLUXION_SIGNTOOL_CMD)
}

function Find-Signtool {
    if ($env:SIGNTOOL -and (Test-Path -LiteralPath $env:SIGNTOOL)) { return $env:SIGNTOOL }
    $onPath = (Get-Command signtool -ErrorAction SilentlyContinue).Source
    if ($onPath) { return $onPath }
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    $found = Get-ChildItem -LiteralPath $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

function Invoke-Sign([string]$file) {
    $file = $file.Trim('"')
    $timestamp = if ($env:FLUXION_SIGN_TIMESTAMP) { $env:FLUXION_SIGN_TIMESTAMP } else { "http://timestamp.digicert.com" }
    if ($env:FLUXION_SIGNTOOL_CMD) {
        $command = $env:FLUXION_SIGNTOOL_CMD.Replace("{file}", '"' + $file + '"')
        Write-Host "[sign] $([IO.Path]::GetFileName($file)): своя команда подписи"
        & cmd.exe /c $command
        if ($LASTEXITCODE -ne 0) { throw "Signing failed ($LASTEXITCODE): $command" }
        return
    }
    $signtool = Find-Signtool
    if (-not $signtool) {
        throw ("signtool.exe не найден. Установите Windows SDK (winget install Microsoft.WindowsSDK.10.0.26100) " +
               "или укажите путь в переменной SIGNTOOL.")
    }
    Write-Host "[sign] $([IO.Path]::GetFileName($file)): signtool, сертификат $env:FLUXION_SIGN_THUMBPRINT"
    & $signtool sign /fd sha256 /sha1 $env:FLUXION_SIGN_THUMBPRINT /tr $timestamp /td sha256 $file
    if ($LASTEXITCODE -ne 0) { throw "signtool failed with code $LASTEXITCODE for $file" }
}

if ($SignOnly) {
    Invoke-Sign $SignOnly
    exit 0
}

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

$isccArgs = @("/Qp", "/DAppVersion=$version", "/DSourceDir=$AppDir", "/DOutputDir=$artifactDir")
if (Test-SigningEnabled) {
    # build_lite.ps1 signs the exe before packing the .7z; a build without it is signed here
    if ((Get-AuthenticodeSignature -LiteralPath $exe).Status -ne "Valid") { Invoke-Sign $exe }
    $self = $PSCommandPath
    $isccArgs += "/DSignToolName=fluxionsign"
    $isccArgs += ('/Sfluxionsign=powershell.exe -NoProfile -ExecutionPolicy Bypass -File $q' + $self + '$q -SignOnly $f')
} else {
    Write-Host "[setup] подпись выключена (нет FLUXION_SIGN_THUMBPRINT / FLUXION_SIGNTOOL_CMD) - установщик без подписи"
}

New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$setup = Join-Path $artifactDir "FluxionSetup-$version.exe"
if (Test-Path -LiteralPath $setup) { Remove-Item -LiteralPath $setup -Force }

Write-Host "[setup] Inno Setup: $iscc" -ForegroundColor Cyan
Write-Host "[setup] building FluxionSetup-$version.exe (a few minutes)..." -ForegroundColor Cyan
& $iscc @isccArgs $script
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $setup)) { throw "ISCC finished but $setup is missing" }

# checksum next to the installer, in the sha256sum format: "<hash> *<file>";
# the in-app update refuses an installer without it
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
$shaFile = "$setup.sha256"
[IO.File]::WriteAllText($shaFile, "$hash *FluxionSetup-$version.exe`n", (New-Object System.Text.UTF8Encoding $false))

$sizeMB = [math]::Round((Get-Item -LiteralPath $setup).Length / 1MB, 1)
Write-Host "[setup] installer: $setup ($sizeMB MB)" -ForegroundColor Green
Write-Host "[setup] SHA256:    $hash" -ForegroundColor Green
