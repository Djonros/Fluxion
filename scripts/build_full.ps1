# Build the Fluxion Browser FULL artifact (Phase 15.3).
#   1) Lite app dir from build_lite.ps1 (dist\FluxionBrowserLite)
#   2) offline wheel pack: torch cu121 + ML stack for Python 3.12
#      (resolved from the local pip cache where possible)
#   3) staging dist\FluxionBrowserFull = Lite + training_pack\wheels
#   4) pack into dist\artifacts\FluxionBrowser-Full-win64-<date>.7z
# Usage: powershell -File scripts\build_full.ps1 [-SkipLiteBuild] [-RefreshWheels]
param(
    [switch]$SkipLiteBuild,
    [switch]$RefreshWheels
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$appDir = Join-Path $root "dist\FluxionBrowserLite"
$fullDir = Join-Path $root "dist\FluxionBrowserFull"
$artifactDir = Join-Path $root "dist\artifacts"
$wheelsDir = Join-Path $root "dist\training_pack_build\wheels"
$py = if ($env:FLUXION_PYTHON) { $env:FLUXION_PYTHON } else { "python" }

if (-not (Test-Path $appDir)) {
    if ($SkipLiteBuild) { throw "Not found: $appDir (run build_lite.ps1 first)" }
    Write-Host "[full] Lite app dir missing - building Lite first..." -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build_lite.ps1")
    if ($LASTEXITCODE -ne 0) { throw "build_lite.ps1 failed with code $LASTEXITCODE" }
}

# --- 2) offline wheel pack (Python 3.12, win64, wheels only) ---
$TRAINING_PACKAGES = @(
    "unsloth", "peft", "trl", "transformers", "datasets",
    "accelerate", "bitsandbytes", "gguf", "cryptography"
)
$torchIndex = "https://download.pytorch.org/whl/cu126"
$torchSpec = "torch==2.12.1+cu126"

$hasTorch = (Test-Path $wheelsDir) -and (Get-ChildItem $wheelsDir -Filter "torch-*.whl" -ErrorAction SilentlyContinue)
if ($RefreshWheels -and (Test-Path $wheelsDir)) {
    Remove-Item $wheelsDir -Recurse -Force
    $hasTorch = $false
}

if (-not $hasTorch) {
    Write-Host "[full] downloading wheel pack ($torchSpec + ML stack, py3.12)..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $wheelsDir | Out-Null
    & $py -m pip download $torchSpec `
        --index-url $torchIndex `
        --python-version 3.12 --only-binary=:all: -d $wheelsDir
    if ($LASTEXITCODE -ne 0) { throw "pip download torch failed with code $LASTEXITCODE" }

    $torchWhl = Get-ChildItem $wheelsDir -Filter "torch-*+cu126-*.whl" |
        Select-Object -First 1
    if (-not $torchWhl) { throw "cu126 torch wheel not found after download" }
    $constraints = Join-Path (Split-Path -Parent $wheelsDir) "constraints.txt"
    "$torchSpec" | Set-Content -Path $constraints -Encoding ASCII
    Write-Host "[full] constraint: $torchSpec" -ForegroundColor Green

    & $py -m pip download @TRAINING_PACKAGES -c $constraints `
        --extra-index-url $torchIndex `
        --python-version 3.12 --only-binary=:all: -d $wheelsDir
    if ($LASTEXITCODE -ne 0) { throw "pip download ML stack failed with code $LASTEXITCODE" }

    $plainTorch = Get-ChildItem $wheelsDir -Filter "*.whl" |
        Where-Object { $_.Name -match "^torch(vision)?-\d" -and $_.Name -notmatch "\+cu126" }
    if ($plainTorch) {
        throw ("CPU torch wheel in pack: " + (($plainTorch | ForEach-Object { $_.Name }) -join ", "))
    }
    $torchLike = Get-ChildItem $wheelsDir -Filter "*.whl" |
        Where-Object { $_.Name -match "^torch(vision)?-\d" }
    Write-Host ("[full] torch wheels: " + (($torchLike | ForEach-Object { $_.Name }) -join ", ")) -ForegroundColor Green
} else {
    Write-Host "[full] reusing wheel pack: $wheelsDir" -ForegroundColor Green
}

function Get-DirMB([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    [math]::Round(((Get-ChildItem $path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum / 1MB), 1)
}

$wheelsMB = Get-DirMB $wheelsDir
$wheelCount = (Get-ChildItem $wheelsDir -Filter *.whl).Count
Write-Host "[full] wheel pack: $wheelCount wheels, $wheelsMB MB" -ForegroundColor Green

# --- 3) staging: Lite + training_pack ---
Write-Host "[full] staging $fullDir (copy of Lite + training_pack)..." -ForegroundColor Cyan
if (Test-Path $fullDir) { Remove-Item $fullDir -Recurse -Force }
Copy-Item -Path $appDir -Destination $fullDir -Recurse
$packDir = Join-Path $fullDir "training_pack"
New-Item -ItemType Directory -Force -Path $packDir | Out-Null
Copy-Item -Path $wheelsDir -Destination (Join-Path $packDir "wheels") -Recurse

$readme = @'
Fluxion offline training pack (Full artifact)

training_pack\wheels contains torch (CUDA cu126) and the full ML stack
(unsloth, peft, trl, transformers, datasets, accelerate, bitsandbytes,
gguf, cryptography) for Python 3.12 / win64.

The app uses it automatically: on the "Training" page the environment
install button runs pip with --no-index --find-links pointing at this
folder, so no internet connection is required. Only Python 3.12 itself
must already be installed (or confirmed via the winget prompt).
'@
Set-Content -Path (Join-Path $packDir "README.txt") -Value $readme -Encoding ASCII
Get-ChildItem $wheelsDir -Filter *.whl | Select-Object -ExpandProperty Name |
    Set-Content -Path (Join-Path $packDir "manifest.txt") -Encoding ASCII

$fullMB = Get-DirMB $fullDir
Write-Host "[full] installed size: $fullMB MB" -ForegroundColor Green

# --- 4) pack ---
$sevenZip = (Get-Command 7z -ErrorAction SilentlyContinue).Source
if (-not $sevenZip) {
    $candidate = "C:\Program Files\7-Zip\7z.exe"
    if (Test-Path $candidate) { $sevenZip = $candidate }
}
if (-not $sevenZip) { throw "7z not found (install 7-Zip or add to PATH)" }

New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
$artifact = Join-Path $artifactDir "FluxionBrowser-Full-win64-$stamp.7z"
if (Test-Path $artifact) { Remove-Item $artifact -Force }

Write-Host "[full] packing 7z (mx=9, this may take a while)..." -ForegroundColor Cyan
& $sevenZip a -mx=9 $artifact "$fullDir\*" -bso0 -bsp0
if ($LASTEXITCODE -ne 0) { throw "7z failed with code $LASTEXITCODE" }

$sizeMB = [math]::Round((Get-Item $artifact).Length / 1MB, 1)
Write-Host "[full] artifact: $artifact" -ForegroundColor Green
Write-Host "[full] installer size: $sizeMB MB (folder: $fullMB MB)" -ForegroundColor Green
