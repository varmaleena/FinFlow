$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path 'apps/web/node_modules')) {
    Push-Location apps/web
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    Pop-Location
}
Push-Location apps/web
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
Pop-Location
python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
