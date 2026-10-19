# One-time setup on Windows: finds Python 3.11+ and runs scripts/setup.py. Usage: .\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.14", "3.13", "3.12", "3.11")) { $candidates += , @("py", "-$v") }
}
if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @("python") }
foreach ($c in $candidates) {
    $exe = $c[0]
    $pyArgs = @($c | Select-Object -Skip 1)
    try {
        $ok = & $exe @pyArgs -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
    } catch { $ok = $null }
    if ($ok -eq "True") {
        & $exe @pyArgs (Join-Path $root "scripts\setup.py") @args
        exit $LASTEXITCODE
    }
}
Write-Host "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/" -ForegroundColor Red
exit 1
