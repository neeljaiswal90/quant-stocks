param(
    [string]$Python = "py -3.12"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$parts = $Python -split " "
$executable = $parts[0]
$prefixArgs = @($parts | Select-Object -Skip 1)

$commands = @(
    @("-m", "ruff", "check", "qme", "tests", "scripts"),
    @("-m", "mypy", "qme", "scripts\verify_lock.py", "scripts\check_secrets.py"),
    @("-m", "pytest", "-q", "-p", "no:cacheprovider"),
    @("-m", "compileall", "-q", "qme", "tests", "scripts"),
    @("scripts\verify_lock.py", "requirements-agent-build.lock", "requirements-runtime.lock", "requirements-dev.lock", "requirements-agents.lock"),
    @("-m", "qme.cli.foundation", "--help"),
    @("-m", "qme.cli.agent_review", "--help")
)

foreach ($arguments in $commands) {
    & $executable @prefixArgs @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "verification failed: $($arguments -join ' ')"
    }
}
