# 增量更新 K 线数据（从缓存末尾续传到最新）
# 用法: .\ensure-data.ps1
# 可安全重复运行 — 已有数据只补新增部分，不重不漏。
# 首次运行从 2021-01-01 全量下载：4h 策略需 250 根暖机、1d 策略需 MA200 暖机，
# 暖机口径的教训见 user_data/paper/FREEZE_V2.md 第八节。

$ErrorActionPreference = "Stop"

$Pairs = @(
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "XRP/USDT:USDT", "BNB/USDT:USDT", "ZEC/USDT:USDT",
    "HOME/USDT:USDT", "BANK/USDT:USDT", "CYS/USDT:USDT",
    "HYPE/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT",
    "AVAX/USDT:USDT", "DOT/USDT:USDT"
)

Write-Host "增量更新 K 线（4h + 1d，futures 模式自动补 funding/mark）..." -ForegroundColor Yellow
Write-Host "品种 ($($Pairs.Count)): $($Pairs -join ' ')" -ForegroundColor Gray
Write-Host ""

freqtrade download-data `
    --exchange binance `
    --trading-mode futures `
    --timeframes 4h 1d `
    --timerange 20210101- `
    --pairs ($Pairs -join " ")

if ($LASTEXITCODE -ne 0) {
    Write-Host "下载失败，请检查网络" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "K 线数据已是最新！" -ForegroundColor Green
