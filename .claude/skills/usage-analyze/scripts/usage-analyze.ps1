[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ProfilerArgs
)

$ErrorActionPreference = "Stop"

# The Skill bundle is self-contained: the profiler is adjacent to this wrapper.
$profilerPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "claude_session_token_profiler.py")).Path

try {
    $pythonCommand = @(Get-Command -Name python -CommandType Application -ErrorAction Stop)[0]
}
catch {
    Write-Error "Python was not found on PATH. Install Python 3 and ensure the 'python' command is available."
    exit 127
}

$pythonPath = if ($pythonCommand.Source) { $pythonCommand.Source } else { $pythonCommand.Path }
$arguments = @($profilerPath)
if ($null -eq $ProfilerArgs -or $ProfilerArgs.Count -eq 0) {
    $arguments += "--current-five-hour"
}
else {
    $arguments += $ProfilerArgs
}

& $pythonPath @arguments
exit $LASTEXITCODE
