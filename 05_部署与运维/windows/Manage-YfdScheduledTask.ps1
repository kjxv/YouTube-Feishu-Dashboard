param(
    [ValidateSet("Install", "Status", "Pause", "Resume", "RunNow", "Diagnose", "Stop", "Uninstall")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$taskName = "YouTube-Feishu-Dashboard-Hourly"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runner = Join-Path $PSScriptRoot "Run-YfdScheduler.ps1"
$python = Join-Path $projectRoot "runtime\venv\Scripts\python.exe"

function Show-Status {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host "INSTALLED=NO"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host "INSTALLED=YES"
    Write-Host "TASK_NAME=$taskName"
    Write-Host "STATE=$($task.State)"
    Write-Host "LAST_RUN=$($info.LastRunTime)"
    Write-Host "LAST_RESULT=$($info.LastTaskResult)"
    Write-Host "NEXT_RUN=$($info.NextRunTime)"
    Write-Host "LOG_FILE=$(Join-Path $projectRoot 'runtime\logs\windows-scheduler.log')"
}

function Show-Diagnostics {
    Show-Status
    Write-Host ""
    Write-Host "RUNNING_SCHEDULER_PROCESSES"
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" `
        -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -like "*youtube_feishu_dashboard scheduler tick*"
        }
    if ($null -eq $processes) {
        Write-Host "NONE"
    }
    else {
        $processes | Select-Object ProcessId, CreationDate, CommandLine | Format-List
    }

    Write-Host ""
    Write-Host "LOCAL_SCHEDULER_DATABASE_STATUS"
    if (Test-Path -LiteralPath $python) {
        Push-Location $projectRoot
        try {
            & $python -m youtube_feishu_dashboard scheduler status
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Host "RUNTIME_MISSING"
    }

    $logFile = Join-Path $projectRoot "runtime\logs\windows-scheduler.log"
    Write-Host ""
    Write-Host "SCHEDULER_LOG_TAIL=$logFile"
    if (Test-Path -LiteralPath $logFile) {
        Get-Content -LiteralPath $logFile -Tail 120
    }
    else {
        Write-Host "LOG_NOT_FOUND"
    }
}

function Install-Task {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Runtime is missing. Run the first-time setup batch first."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
        throw ".env is missing. Run the first-time setup batch first."
    }
    & $python --version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The copied runtime cannot run on this computer. Re-run first-time setup."
    }

    $nextHour = (Get-Date).AddHours(1)
    $nextHour = Get-Date -Year $nextHour.Year -Month $nextHour.Month -Day $nextHour.Day `
        -Hour $nextHour.Hour -Minute 0 -Second 0
    $quotedRunner = '"' + $runner + '"'
    $taskAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $quotedRunner" `
        -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At $nextHour `
        -RepetitionInterval (New-TimeSpan -Hours 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
    $userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal `
        -UserId $userName `
        -LogonType Interactive `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $taskName `
        -Description "Hourly wake-up; API cadence is decided inside the application." `
        -Action $taskAction `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
    Write-Host "ACTION=INSTALLED"
    Show-Status
}

function Pause-Task {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
    Write-Host "ACTION=PAUSED"
    Show-Status
}

function Resume-Task {
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    Write-Host "ACTION=RESUMED"
    Show-Status
}

function Run-TaskNow {
    Start-ScheduledTask -TaskName $taskName
    Write-Host "ACTION=STARTED"
}

function Stop-RunningTask {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host "ACTION=NOT_INSTALLED"
        return
    }
    if ($task.State -ne "Running") {
        Write-Host "ACTION=NOT_RUNNING"
        Show-Status
        return
    }
    Stop-ScheduledTask -TaskName $taskName
    Write-Host "ACTION=STOPPED"
    Show-Status
}

function Uninstall-Task {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Host "ACTION=NOT_INSTALLED"
        return
    }
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "ACTION=UNINSTALLED"
}

switch ($Action) {
    "Install" { Install-Task }
    "Status" { Show-Status }
    "Pause" { Pause-Task }
    "Resume" { Resume-Task }
    "RunNow" { Run-TaskNow }
    "Diagnose" { Show-Diagnostics }
    "Stop" { Stop-RunningTask }
    "Uninstall" { Uninstall-Task }
}
