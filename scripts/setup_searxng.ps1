# scripts/setup_searxng.ps1
# Starts a local SearXNG instance in Docker with JSON output enabled.
#
# Usage:
#   powershell -File scripts/setup_searxng.ps1
#
# Prerequisites: Docker Desktop running.

$ErrorActionPreference = "Stop"

$ContainerName = "fluxion-searxng"
$Port = 8080
$DataDir = "$PSScriptRoot\..\data\searxng"

Write-Host "Setting up SearXNG in Docker..." -ForegroundColor Cyan

# Create data directory
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}

# Write settings.yml patch to enable JSON format
$SettingsYml = @"
use_default_settings: true

server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "fluxion-local-dev-key-change-me"

search:
  formats:
    - html
    - json
  safe_search: 0
  autocomplete: ""

engines:
  - name: duckduckgo
    engine: duckduckgo
  - name: google
    engine: google
  - name: bing
    engine: bing
  - name: wikipedia
    engine: wikipedia
"@

$SettingsPath = Join-Path $DataDir "settings.yml"
Set-Content -Path $SettingsPath -Value $SettingsYml -Encoding UTF8

# Remove existing container
$existing = docker ps -a --filter "name=$ContainerName" --format "{{.ID}}" 2>$null
if ($existing) {
    Write-Host "Removing existing container..." -ForegroundColor Yellow
    docker rm -f $ContainerName | Out-Null
}

# Run SearXNG
Write-Host "Starting SearXNG on http://localhost:$Port ..." -ForegroundColor Cyan
docker run -d `
    --name $ContainerName `
    -p "${Port}:8080" `
    -v "${SettingsPath}:/etc/searxng/settings.yml:ro" `
    -e "SEARXNG_BASE_URL=http://localhost:$Port/" `
    --restart unless-stopped `
    searxng/searxng:latest

Start-Sleep -Seconds 3

# Health check
Write-Host "Checking health..." -ForegroundColor Cyan
try {
    $response = Invoke-RestMethod -Uri "http://localhost:$Port/search?q=test&format=json" -TimeoutSec 10
    Write-Host "SearXNG is running! Results: $($response.results.Count)" -ForegroundColor Green
    Write-Host ""
    Write-Host "Configure your assistant:" -ForegroundColor Cyan
    Write-Host "  searxng_url: http://localhost:$Port"
} catch {
    Write-Warning "SearXNG may still be starting. Wait a few seconds and retry:"
    Write-Host "  curl 'http://localhost:$Port/search?q=test&format=json'" -ForegroundColor Gray
}
