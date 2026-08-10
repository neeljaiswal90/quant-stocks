param(
    [string]$Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$parts = $Python -split " "
$executable = $parts[0]
$prefixArgs = @($parts | Select-Object -Skip 1)

& $executable @prefixArgs -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }

$venvPython = Join-Path $PWD ".venv\Scripts\python.exe"
& $venvPython -m pip install --require-hashes -r requirements-dev.lock
if ($LASTEXITCODE -ne 0) { throw "locked dependency install failed" }
& $venvPython -m pip install --no-build-isolation --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw "QME editable install failed" }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "pip check failed" }
