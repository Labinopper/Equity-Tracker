# Equity Tracker — dev startup (plain SQLite, file persists between runs)
# Run from the equity_tracker\ directory:
#   .\start_dev.ps1

$env:EQUITY_DB_PATH      = "$PSScriptRoot\dev.db"
$env:EQUITY_DB_ENCRYPTED = "false"

Write-Host "Starting Equity Tracker [DEV]  →  http://localhost:8000" -ForegroundColor Yellow
Write-Host "DB: $env:EQUITY_DB_PATH" -ForegroundColor DarkYellow
python run_api.py
