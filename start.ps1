# Start RepoGuide on Windows (backend + frontend). Usage: .\start.ps1   or   .\start.ps1 --prod
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "No .venv found. Run .\setup.ps1 first." -ForegroundColor Yellow
    exit 1
}
& $python (Join-Path $root "scripts\dev.py") @args
exit $LASTEXITCODE
