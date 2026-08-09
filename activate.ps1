# Freqtrade 环境激活脚本
# 使用方法: powershell -ExecutionPolicy Bypass -File activate.ps1
# 或者在 PowerShell 中: . .\activate.ps1

$env:PYTHONIOENCODING = "utf-8"
Set-Location "C:\Users\sieata\Documents\freqtrade"
.\.venv\Scripts\Activate.ps1
Write-Host "Freqtrade 环境已激活!" -ForegroundColor Green
Write-Host "当前目录: $(Get-Location)" -ForegroundColor Cyan
Write-Host ""
Write-Host "常用命令:" -ForegroundColor Yellow
Write-Host "  freqtrade list-strategies          - 列出可用策略"
Write-Host "  freqtrade new-strategy --strategy MyStrategy  - 创建新策略"
Write-Host "  freqtrade download-data -t 5m -p BTC/USDT      - 下载数据"
Write-Host "  freqtrade backtesting --strategy SampleStrategy - 回测"
Write-Host "  freqtrade trade --dry-run            - 模拟交易"
Write-Host "  freqtrade webserver                  - 启动Web管理界面"
Write-Host ""

# 自动检查数据，缺失则静默下载
$DataDir = "$PSScriptRoot\user_data\data"
$hasData = Get-ChildItem -Path $DataDir -File -ErrorAction SilentlyContinue
if (-not $hasData) {
    Write-Host "K线数据缺失，自动下载中..." -ForegroundColor Yellow
    & "$PSScriptRoot\ensure-data.ps1"
}
