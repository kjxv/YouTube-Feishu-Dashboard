$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot "runtime\venv\Scripts\python.exe"
$logDirectory = Join-Path $projectRoot "runtime\logs"
$logFile = Join-Path $logDirectory "windows-scheduler.log"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Runtime is missing. Run the first-time setup batch before scheduling."
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Add-Content -LiteralPath $logFile -Value "`r`n[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] scheduled check started"

Push-Location $projectRoot
try {
    & $python -m youtube_feishu_dashboard scheduler scheduled-run latest-video-tracker *>> $logFile
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
