param(
    [string]$Address = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    if ([string]::IsNullOrWhiteSpace($Address)) {
        & $venvPython -m instax_lab
    } else {
        & $venvPython -m instax_lab $Address
    }
} else {
    Write-Host "[run.ps1] .venv not found, using system python." -ForegroundColor Yellow
    if ([string]::IsNullOrWhiteSpace($Address)) {
        python -m instax_lab
    } else {
        python -m instax_lab $Address
    }
}
