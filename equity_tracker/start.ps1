# Equity Tracker — production startup
# Run from the equity_tracker\ directory:
#   .\start.ps1

$env:EQUITY_DB_PATH      = "C:\Users\labin\portfolio.db"
$env:EQUITY_DB_ENCRYPTED = "false"

Write-Host "Starting Equity Tracker  →  http://localhost:8000" -ForegroundColor Cyan
& "$PSScriptRoot\.venv\Scripts\python.exe" run_api.py
