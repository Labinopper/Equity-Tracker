$ErrorActionPreference = "Stop"

$taskName = "EquityTracker-AutoDeploy"
$scriptPath = Join-Path $PSScriptRoot "auto_update_and_restart.ps1"

if (!(Test-Path $scriptPath)) {
    throw "Missing script: $scriptPath"
}

$taskRun = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

schtasks.exe /Create /SC MINUTE /MO 1 /TN $taskName /TR $taskRun /F | Out-Null
schtasks.exe /Run /TN $taskName | Out-Null

Write-Host "Scheduled task '$taskName' created and started (runs every 1 minute)."
