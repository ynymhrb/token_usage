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
$currentSessionFlag = "--current-session"
if ($null -eq $ProfilerArgs -or $ProfilerArgs.Count -eq 0) {
    $ProfilerArgs = @($currentSessionFlag)
}
if ($ProfilerArgs -contains $currentSessionFlag) {
    if ($ProfilerArgs.Count -ne 1) {
        Write-Error "--current-session cannot be combined with other profiler arguments."
        exit 2
    }
    $resolverPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "current_session.py")).Path
    $sessionId = (& $pythonPath $resolverPath "--cwd" (Get-Location).Path).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sessionId)) {
        if ($LASTEXITCODE -eq 0) { exit 2 }
        exit $LASTEXITCODE
    }
    $arguments += $sessionId
}
else {
    $arguments += $ProfilerArgs
}

& $pythonPath @arguments
exit $LASTEXITCODE
