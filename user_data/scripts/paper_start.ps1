# WeekendReverseV1 forward-test launcher (paper trading / dry-run)
# Usage (from project root):  .\user_data\scripts\paper_start.ps1
# Prereq: fill Binance API key in user_data\config_paper.json (see paper\FREEZE.md)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # user_data\scripts -> project root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Config = Join-Path $Root "user_data\config_paper.json"
$LogDir = Join-Path $Root "user_data\logs"
$LogFile = Join-Path $LogDir "paper_forwardtest.log"

# 0. preflight: python + config exist
if (-not (Test-Path $Python)) { Write-Error "python not found: $Python"; exit 1 }
if (-not (Test-Path $Config))  { Write-Error "config not found: $Config"; exit 1 }

# 1. preflight: API key filled?
$raw = Get-Content $Config -Raw | ConvertFrom-Json
if ($raw.exchange.key -like "YOUR_*" -or $raw.exchange.secret -like "YOUR_*") {
    Write-Host "[!] Binance API key not set. Edit: $Config" -ForegroundColor Yellow
    Write-Host "    See: user_data\paper\FREEZE.md" -ForegroundColor Yellow
    exit 1
}

# 2. (optional) pre-download 4h history to speed up first warmup
Write-Host ">> pre-downloading 4h history (slow first run; Ctrl+C to skip)..." -ForegroundColor Cyan
& $Python -m freqtrade download-data --config $Config --timerange 20240101- --timeframe 4h 2>&1 | Out-Null
Write-Host ">> data ready" -ForegroundColor Cyan

# 3. launch dry-run in background, log to file
New-Item -ItemType Directory -Force $LogDir | Out-Null
$argList = @("-m","freqtrade","trade","--config",$Config,"--strategy","WeekendReverseV1","--logfile",$LogFile)
$proc = Start-Process -FilePath $Python -ArgumentList $argList -WorkingDirectory $Root -WindowStyle Hidden -PassThru

Write-Host ""
Write-Host "[OK] forward-test started (PID $($proc.Id))" -ForegroundColor Green
Write-Host "    log:      $LogFile" -ForegroundColor Green
Write-Host "    evaluate: .venv\Scripts\python.exe user_data\scripts\paper_status.py" -ForegroundColor Green
Write-Host "    stop:     Stop-Process -Id $($proc.Id)" -ForegroundColor Green
Write-Host ""
Write-Host "note: verify the strategy SHA in the log header against paper\FREEZE.md." -ForegroundColor DarkGray
