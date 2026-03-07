$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$appDir = Join-Path $repoRoot "equity_tracker"
$pythonExe = Join-Path $appDir ".venv\Scripts\python.exe"
$startScript = Join-Path $appDir "start.ps1"
$gitExe = "C:\Program Files\Git\cmd\git.exe"
$pidFile = Join-Path $repoRoot ".equity_tracker.pid"
$lockFile = Join-Path $repoRoot ".autodeploy.lock"
$logDir = Join-Path $repoRoot "logs"
$stdoutLog = Join-Path $logDir "equity_tracker.out.log"
$stderrLog = Join-Path $logDir "equity_tracker.err.log"

function Write-Log([string]$message) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$ts] $message"
}

function Get-UpstreamBranch {
    try {
        return (& $gitExe -C $repoRoot rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null).Trim()
    }
    catch {
        return ""
    }
}

function Get-RepoDirtyState {
    $output = & $gitExe -C $repoRoot status --porcelain --untracked-files=no 2>$null
    if ($null -eq $output) {
        return ""
    }
    return (($output | Out-String).Trim())
}

function Get-ListenPid {
    $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $conn) {
        return [int]$conn.OwningProcess
    }
    return $null
}

function Is-TrackerProcess([int]$processId) {
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        if ($null -eq $proc) { return $false }
        $cmd = [string]$proc.CommandLine
        return ($cmd -match '(^|[\"'' ])run_api\.py($|[\"'' ])')
    }
    catch {
        return $false
    }
}

function Stop-TrackerProcess {
    if (Test-Path $pidFile) {
        $raw = (Get-Content -Raw $pidFile).Trim()
        if ($raw -match "^\d+$") {
            $trackedProcessId = [int]$raw
            if (Is-TrackerProcess -processId $trackedProcessId) {
                Write-Log "Stopping tracked process PID $trackedProcessId"
                Stop-Process -Id $trackedProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }

    $listenPid = Get-ListenPid
    if ($null -ne $listenPid -and (Is-TrackerProcess -processId $listenPid)) {
        Write-Log "Stopping listener process PID $listenPid"
        Stop-Process -Id $listenPid -Force -ErrorAction SilentlyContinue
    }
}

function Start-TrackerProcess {
    if (!(Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir | Out-Null
    }
    $proc = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript `
        -WorkingDirectory $appDir `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog
    Set-Content -Path $pidFile -Value $proc.Id -NoNewline
    Write-Log "Started Equity Tracker PID $($proc.Id)"
}

if (Test-Path $lockFile) {
    $ageMinutes = ((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalMinutes
    if ($ageMinutes -lt 10) {
        exit 0
    }
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}
Set-Content -Path $lockFile -Value $PID -NoNewline

try {
    if (!(Test-Path $pythonExe)) {
        throw "Python venv not found at $pythonExe"
    }
    if (!(Test-Path $gitExe)) {
        throw "Git not found at $gitExe"
    }

    $dirty = Get-RepoDirtyState
    $upstream = Get-UpstreamBranch
    $restartRequired = $false

    if ($upstream -ne "") {
        & $gitExe -C $repoRoot fetch origin --prune | Out-Null
        $localSha = (& $gitExe -C $repoRoot rev-parse HEAD).Trim()
        $remoteSha = (& $gitExe -C $repoRoot rev-parse $upstream).Trim()

        if ($localSha -ne $remoteSha) {
            if ($dirty -ne "") {
                Write-Log "Updates available but working tree is dirty; skipping pull."
            }
            else {
                Write-Log "Pulling updates from $upstream"
                & $gitExe -C $repoRoot pull --ff-only
                $restartRequired = $true
            }
        }
    }

    $listenPid = Get-ListenPid
    if ($restartRequired) {
        Stop-TrackerProcess
        Start-TrackerProcess
    }
    elseif ($null -eq $listenPid) {
        Write-Log "App is not listening on port 8000; starting it."
        Start-TrackerProcess
    }
}
finally {
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}
