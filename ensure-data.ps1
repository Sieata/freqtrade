# 自动检查并下载 K 线数据（数据缺失时自动补全）
# 用法: .\ensure-data.ps1
# 可安全重复运行 — 已有数据则跳过

$ErrorActionPreference = "Stop"

$DataDir = "user_data\data"
$Pairs = @(
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "XRP/USDT:USDT", "BNB/USDT:USDT", "ZEC/USDT:USDT",
    "HOME/USDT:USDT", "BANK/USDT:USDT", "CYS/USDT:USDT",
    "HYPE/USDT:USDT", "DOGE/USDT:USDT"
)

# 检查数据是否已存在
$existing = Get-ChildItem -Path $DataDir -File -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "已有 $($existing.Count) 个数据文件，跳过下载" -ForegroundColor Green
    exit 0
}

Write-Host "数据目录为空，开始下载..." -ForegroundColor Yellow
Write-Host "品种 ($($Pairs.Count)): $($Pairs -join ' ')" -ForegroundColor Gray
Write-Host ""

freqtrade download-data `
    --exchange binance `
    --trading-mode futures `
    --timeframe 4h `
    --timerange 20210101- `
    --pairs ($Pairs -join " ")

if ($LASTEXITCODE -ne 0) {
    Write-Host "下载失败，请检查网络" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "数据下载完成！" -ForegroundColor Green
