# Rebuild and restart the Docker stack stamped with the current commit.
Set-Location $PSScriptRoot
$env:BUILD = (git rev-parse --short HEAD)
docker compose up --build -d
Write-Host "stack built from $env:BUILD"
