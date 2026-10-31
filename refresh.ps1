# 数据保鲜入口（Windows PowerShell）：等价于 ./refresh_all.sh
#
# 用法（可安全重复运行）：
#   .\refresh.ps1
#   $env:FT_PROXY = "none"; .\refresh.ps1        # 强制直连
#   $env:FT_PROXY = "http://127.0.0.1:7897"; .\refresh.ps1
#
# 实际逻辑在 refresh_all.sh（单一实现，避免 PowerShell/bash 双份维护）。
# 注意：不要设 $ErrorActionPreference = "Stop" —— PS 5.1 会把 native exe 的 stderr
# （freqtrade 日志）包装成终止错误；退出码统一用 $LASTEXITCODE 判断。

$root = $PSScriptRoot

$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
if (-not (Test-Path $bash)) {
    $cmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($cmd) { $bash = $cmd.Source }
}
if (-not $bash -or -not (Test-Path $bash)) {
    Write-Error "找不到 Git Bash（bash.exe），请先安装 Git for Windows"
    exit 1
}

& $bash (Join-Path $root "refresh_all.sh") @args
exit $LASTEXITCODE
