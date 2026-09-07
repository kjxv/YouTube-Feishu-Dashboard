$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot "runtime\venv\Scripts\python.exe"
$logDirectory = Join-Path $projectRoot "runtime\logs"
$logFile = Join-Path $logDirectory "windows-scheduler.log"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Runtime is missing. Run the first-time setup batch before scheduling."
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$startedAt = Get-Date
$exitCode = 1
Add-Content -LiteralPath $logFile -Value (
    "`r`n[TICK_START] time={0} wrapper_pid={1}" -f `
        $startedAt.ToString("yyyy-MM-dd HH:mm:ss zzz"), $PID
)

Push-Location $projectRoot
try {
    & $python -m youtube_feishu_dashboard scheduler tick *>> $logFile
    $exitCode = $LASTEXITCODE
}
catch {
    $exitCode = 1
    Add-Content -LiteralPath $logFile -Value (
        "[TICK_WRAPPER_ERROR] type={0} message={1}" -f `
            $_.Exception.GetType().Name, $_.Exception.Message
    )
}
finally {
    $finishedAt = Get-Date
    $durationSeconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 1)
    Add-Content -LiteralPath $logFile -Value (
        "[TICK_END] time={0} exit_code={1} duration_seconds={2}" -f `
            $finishedAt.ToString("yyyy-MM-dd HH:mm:ss zzz"), `
            $exitCode, `
            $durationSeconds
    )
    Pop-Location
}

exit $exitCode
