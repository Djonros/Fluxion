# Build the Microsoft Store package: dist\artifacts\Fluxion-<version>-x64.msix.
#   Runs after build_lite.ps1: packs dist\FluxionBrowserLite into an MSIX with
#   installer\msix\AppxManifest.xml. The version comes from
#   desktop_browser\__init__.py (0.28.0 -> 0.28.0.0: the Store wants the
#   fourth number 0).
# Usage: powershell -File scripts\build_msix.ps1 [-AppDir <folder>] [-Identity <json>] [-OutDir <folder>]
#   -Identity  installer\msix\store-identity.json by default: Identity Name,
#              Publisher and PublisherDisplayName from Partner Center
#              (Product management -> Product identity). Without it the
#              placeholders of store-identity.example.json are used: such a
#              package is good for a check of the build, not for the Store.
#   $env:WINDOWS_SDK_BIN  folder with makeappx.exe / makepri.exe when the
#              Windows SDK is somewhere unusual
#               (winget install Microsoft.WindowsSDK.10.0.26100)
# The package is NOT signed: Partner Center signs it on upload. To install it
# locally before that, see desktop_browser\notes\Памятка - Microsoft Store.md.
param([string]$AppDir = "", [string]$Identity = "", [string]$OutDir = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $AppDir) { $AppDir = Join-Path $root "dist\FluxionBrowserLite" }
if (-not $OutDir) { $OutDir = Join-Path $root "dist\artifacts" }
$exe = Join-Path $AppDir "FluxionBrowserLite.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Нет $exe - сначала соберите программу (fluxion-build.bat --lite)."
}

function Find-SdkTool([string]$name) {
    if ($env:WINDOWS_SDK_BIN) {
        $p = Join-Path $env:WINDOWS_SDK_BIN $name
        if (Test-Path -LiteralPath $p) { return $p }
    }
    $onPath = (Get-Command $name -ErrorAction SilentlyContinue).Source
    if ($onPath) { return $onPath }
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    $found = Get-ChildItem -LiteralPath $kits -Recurse -Filter $name -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) { return $found.FullName }
    throw "$name не найден. Установите Windows SDK: winget install Microsoft.WindowsSDK.10.0.26100"
}

function Escape-Xml([string]$s) { return [System.Security.SecurityElement]::Escape($s) }

# ── version ──────────────────────────────────────────────────────────────
$init = Get-Content -LiteralPath (Join-Path $root "desktop_browser\__init__.py") -Raw -Encoding UTF8
if ($init -notmatch 'APP_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"') { throw "APP_VERSION не найдена в desktop_browser\__init__.py" }
$version = "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"

# ── identity ─────────────────────────────────────────────────────────────
if (-not $Identity) { $Identity = Join-Path $root "installer\msix\store-identity.json" }
$forStore = Test-Path -LiteralPath $Identity
if (-not $forStore) {
    Write-Host "[msix] ВНИМАНИЕ: нет $Identity - беру заглушки из store-identity.example.json." -ForegroundColor Yellow
    Write-Host "[msix]          Такой пакет годится для проверки сборки, но не для загрузки в Store." -ForegroundColor Yellow
    $Identity = Join-Path $root "installer\msix\store-identity.example.json"
}
$id = Get-Content -LiteralPath $Identity -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($field in "IdentityName", "Publisher", "PublisherDisplayName", "DisplayName", "Description") {
    if (-not $id.$field) { throw "В $Identity не заполнено поле $field" }
}
if ($id.Publisher -notmatch '^CN=') { throw "Publisher должен начинаться с CN= (копируйте из Partner Center целиком)" }

# ── layout: the program folder + images + manifest ───────────────────────
$work = Join-Path $root "dist\msix"
$layout = Join-Path $work "layout"
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $layout | Out-Null

Write-Host "[msix] копирую $AppDir"
# portable.txt would keep the data in the read-only WindowsApps folder; data/config
# are the user's; unins*.exe would make it look like a FluxionSetup install
& robocopy $AppDir $layout /E /NFL /NDL /NJH /NJS /NP /XF portable.txt "unins*.*" /XD data config | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy завершился с кодом $LASTEXITCODE" }

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "Нужен python с Pillow для картинок пакета (scripts\msix_assets.py)" }
# images go to a folder of their own first: makepri indexes it (names "Assets\...")
# without walking the thousands of files of _internal; then they join the layout
$priSrc = Join-Path $work "pri-src"
& $python (Join-Path $root "scripts\msix_assets.py") $priSrc
if ($LASTEXITCODE -ne 0) { throw "scripts\msix_assets.py завершился с кодом $LASTEXITCODE" }
Copy-Item -LiteralPath (Join-Path $priSrc "Assets") -Destination $layout -Recurse

$manifest = Get-Content -LiteralPath (Join-Path $root "installer\msix\AppxManifest.xml") -Raw -Encoding UTF8
$values = @{
    "IdentityName" = $id.IdentityName; "Publisher" = $id.Publisher; "Version" = $version
    "PublisherDisplayName" = $id.PublisherDisplayName; "DisplayName" = $id.DisplayName
    "Description" = $id.Description
}
foreach ($key in $values.Keys) { $manifest = $manifest.Replace("{{$key}}", (Escape-Xml $values[$key])) }
if ($manifest -match '\{\{\w+\}\}') { throw "В манифесте остались незаполненные поля: $($Matches[0])" }
[IO.File]::WriteAllText((Join-Path $layout "AppxManifest.xml"), $manifest, (New-Object Text.UTF8Encoding $false))

# ── resources.pri: plain image names -> scale/targetsize files ───────────
$makepri = Find-SdkTool "makepri.exe"
# one resources.pri (no split by scale: that is for bundles) over pri-src = Assets
$priConfig = Join-Path $work "priconfig.xml"
$priXml = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<resources targetOsVersion="10.0.0" majorVersion="1">
  <index root="\" startIndexAt="\">
    <default>
      <qualifier name="Language" value="ru-RU;en-US"/>
      <qualifier name="Contrast" value="standard"/>
      <qualifier name="Scale" value="100"/>
      <qualifier name="HomeRegion" value="001"/>
      <qualifier name="TargetSize" value="256"/>
      <qualifier name="LayoutDirection" value="LTR"/>
      <qualifier name="Theme" value="dark"/>
      <qualifier name="AlternateForm" value=""/>
      <qualifier name="DXFeatureLevel" value="DX9"/>
      <qualifier name="Configuration" value=""/>
      <qualifier name="DeviceFamily" value="Universal"/>
      <qualifier name="Custom" value=""/>
    </default>
    <indexer-config type="folder" foldernameAsQualifier="true" filenameAsQualifier="true" qualifierDelimiter="."/>
  </index>
</resources>
'@
[IO.File]::WriteAllText($priConfig, $priXml, (New-Object Text.UTF8Encoding $false))
& $makepri new /pr $priSrc /cf $priConfig /mn (Join-Path $layout "AppxManifest.xml") /of (Join-Path $layout "resources.pri") /o | Out-Null
if ($LASTEXITCODE -ne 0) { throw "makepri new: код $LASTEXITCODE" }

# ── pack ─────────────────────────────────────────────────────────────────
$makeappx = Find-SdkTool "makeappx.exe"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$package = Join-Path $OutDir ("Fluxion-{0}-x64.msix" -f $version)
Write-Host "[msix] упаковываю (несколько минут)..."
$packLog = & $makeappx pack /d $layout /p $package /o 2>&1
if ($LASTEXITCODE -ne 0) {
    $packLog | Select-Object -Last 30 | ForEach-Object { Write-Host $_ }
    throw "makeappx pack: код $LASTEXITCODE"
}

$size = [math]::Round((Get-Item -LiteralPath $package).Length / 1MB)
Write-Host ""
Write-Host "[msix] Готово: $package ($size МБ, версия $version)" -ForegroundColor Green
if ($forStore) {
    Write-Host "[msix] Загрузите его в Partner Center: Отправка -> Пакеты. Подписывать не нужно."
} else {
    Write-Host "[msix] Это пробный пакет с заглушками вместо данных Partner Center." -ForegroundColor Yellow
}
