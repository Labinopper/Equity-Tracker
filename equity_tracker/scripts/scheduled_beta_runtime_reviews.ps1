$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonScript = Join-Path $PSScriptRoot "beta_runtime_review.py"
$logPath = Join-Path $projectRoot "logs\beta_runtime_review_scheduler.log"
$delaysMinutes = @(60, 120)

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
Add-Content -Path $logPath -Value ("[{0}] Scheduled beta runtime reviews started" -f (Get-Date -Format o))

Set-Location $projectRoot

$elapsedMinutes = 0
foreach ($delay in $delaysMinutes) {
    $sleepSeconds = [Math]::Max(0, ($delay - $elapsedMinutes) * 60)
    if ($sleepSeconds -gt 0) {
        Start-Sleep -Seconds $sleepSeconds
    }

    Add-Content -Path $logPath -Value ("[{0}] Running scheduled review {1}" -f (Get-Date -Format o), $delay)
    & python $pythonScript --label ("plus_{0}m" -f $delay) --attempt-restart 2>&1 | Tee-Object -FilePath $logPath -Append
    $elapsedMinutes = $delay
}

Add-Content -Path $logPath -Value ("[{0}] Scheduled beta runtime reviews completed" -f (Get-Date -Format o))
