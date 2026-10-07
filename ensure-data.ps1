# 增量更新 K 线数据（从缓存末尾续传到最新）— Windows PowerShell 版（对应 ensure-data.sh）
# 用法:
#   .\ensure-data.ps1                                     # 默认 14 个既有品种
#   .\ensure-data.ps1 user_data\universe\pairs_top10.txt   # 按币池快照补数据（支持行内 # 注释）
#   执行策略受限时: powershell -ExecutionPolicy Bypass -File ensure-data.ps1 [pairs文件]
# 可安全重复运行 — 已有数据只补新增部分，不重不漏。跑完建议接数据接缝校验:
#   .\.venv\Scripts\python.exe user_data\scripts\data_check.py --pools top10
#
# 网络：本机直连 binance API 不通，走代理 127.0.0.1:7897（Clash）。
# 环境变量 FT_PROXY 可覆盖代理地址；FT_PROXY=none 直连。
# 新品种的 funding 老数据会被 WAF 403 拦，另用 user_data\scripts\import_funding_vision.py 补。
#
# 2026-10-07 修复：原版调用 PATH 里的 freqtrade（不是 .venv）、不设代理、
# 把全部品种拼成一个字符串当单个参数传入，且文件无 BOM（Windows PowerShell 5.1 读中文乱码）。

param([string]$PairsFile = "")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "[!] 找不到 $Py（先运行 setup.ps1 或 uv venv .venv --python 3.12）" -ForegroundColor Red
    exit 1
}

$Proxy = if ($env:FT_PROXY) { $env:FT_PROXY } else { "http://127.0.0.1:7897" }
if ($Proxy -ne "none") {
    $env:https_proxy = $Proxy
    $env:http_proxy = $Proxy
}
$env:PYTHONIOENCODING = "utf-8"

$Pairs = @(
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "XRP/USDT:USDT", "BNB/USDT:USDT", "ZEC/USDT:USDT",
    "HOME/USDT:USDT", "BANK/USDT:USDT", "CYS/USDT:USDT",
    "HYPE/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT",
    "AVAX/USDT:USDT", "DOT/USDT:USDT"
)

if ($PairsFile) {
    if (-not (Test-Path $PairsFile)) {
        Write-Host "[!] 找不到 pairs 文件: $PairsFile" -ForegroundColor Red
        exit 1
    }
    $Pairs = @(Get-Content -Encoding UTF8 $PairsFile |
        ForEach-Object { ($_ -split "#", 2)[0].Trim() } |
        Where-Object { $_ })
    Write-Host "从 $PairsFile 读取 $($Pairs.Count) 个品种"
}

Write-Host "增量更新 K 线（4h + 1d，futures 模式自动补 funding/mark）..." -ForegroundColor Yellow
Write-Host "品种 ($($Pairs.Count)): $($Pairs -join ' ')" -ForegroundColor Gray
Write-Host "代理: $Proxy" -ForegroundColor Gray
Write-Host ""

# 数组逐项展开为独立参数（原版 ($Pairs -join " ") 会变成一个带空格的参数）
$ArgList = @("-m", "freqtrade", "download-data",
    "--exchange", "binance",
    "--trading-mode", "futures",
    "--timeframes", "4h", "1d",
    "--timerange", "20210101-",
    "--pairs") + $Pairs
& $Py @ArgList

if ($LASTEXITCODE -ne 0) {
    Write-Host "下载失败（exit $LASTEXITCODE），检查代理 $Proxy 是否在线" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "K 线数据已是最新！" -ForegroundColor Green
