# api.js 端到端验证：真实启动 uvicorn，用 Node 加载 app/static/js/api.js 打真接口。
# 独立临时数据库，不污染 data/cuotiben.db。
#
# 为什么用 Node 而不是 curl：本步的交付物就是 api.js，
# 要验证的是"这份封装与后端契约一致"，所以必须真的把它加载起来、真的发请求。
# 断言逻辑在 tools/verify_api_js.js 里，便于用 node --check 单独做语法检查。
#
# 沿用 curl_*.ps1 的做法：自动挑空闲端口、临时库、结束后杀进程并清理。
# 本文件必须存为 UTF-8 with BOM。

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = 'C:\Users\yinxu\AppData\Local\Programs\Python\Python313\python.exe'
$node = 'C:\Program Files\nodejs\node.exe'

if (-not (Test-Path $node)) {
    $found = Get-Command node -ErrorAction SilentlyContinue
    if ($found) { $node = $found.Source } else { throw "未找到 node，无法运行 api.js 自检" }
}

$tmp = Join-Path $env:TEMP ("cuotiben_api_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$dbFile = Join-Path $tmp 'api.db'

# 挑一个空闲端口
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()

$env:CUOTIBEN_DATABASE_URL = "sqlite:///" + ($dbFile -replace '\\', '/')
$base = "http://127.0.0.1:$port"

Write-Host "临时库: $dbFile"
Write-Host "端口  : $port"
Write-Host ('=' * 78)

$proc = Start-Process -FilePath $py -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $tmp 'out.log') `
    -RedirectStandardError (Join-Path $tmp 'err.log') `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$port", '--log-level', 'warning')

try {
    # 等服务起来（最多 30s）
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $r = Invoke-WebRequest -Uri "$base/health" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Write-Host "服务未能在 30s 内启动，日志：" -ForegroundColor Red
        Get-Content (Join-Path $tmp 'err.log') -ErrorAction SilentlyContinue | Select-Object -Last 20
        throw "uvicorn 启动失败"
    }
    Write-Host "服务已就绪: $base"
    Write-Host ""

    & $node 'tools/verify_api_js.js' $base
    $code = $LASTEXITCODE
    Write-Host ""
    if ($code -eq 0) {
        Write-Host "api.js 自检全部通过" -ForegroundColor Green
    } else {
        Write-Host "api.js 自检存在失败项（退出码 $code）" -ForegroundColor Red
    }
    exit $code
} finally {
    if ($proc -and -not $proc.HasExited) { $proc.Kill() }
    Start-Sleep -Milliseconds 300
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "服务已停止，临时目录已清理"
}
