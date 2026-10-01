# folders 竖线端到端验证：真实启动 uvicorn，用 curl 打接口。
# 使用独立的临时数据库，不污染 data/cuotiben.db。
#
# 两个 Windows PowerShell 5.1 的坑（都在这里绕开了）：
#   1. `curl` 是 Invoke-WebRequest 的别名，优先级高于同名函数 -> 用 Invoke-Curl 并显式调 curl.exe
#   2. 非 ASCII 作为命令行参数传给原生 exe 时会被按 ANSI 代码页转换而损坏
#      -> JSON 载荷一律写进临时文件，用 curl 的 `-d @file` 传，绕开参数编码
# 另外 Get-Content -Encoding utf8 会给内容加 BOM，导致 ConvertFrom-Json 失败，故用 .NET 读取。

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = 'C:\Users\yinxu\AppData\Local\Programs\Python\Python313\python.exe'
$tmp = Join-Path $env:TEMP ("cuotiben_curl_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$jsonDir = Join-Path $tmp 'json'
New-Item -ItemType Directory -Force -Path $jsonDir | Out-Null
$dbFile = Join-Path $tmp 'curl.db'

# 找空闲端口
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

$noBom = New-Object System.Text.UTF8Encoding($false)
$script:jsonSeq = 0

# 把 JSON 写入临时文件（UTF-8 无 BOM），返回 curl 的 @file 参数
function New-JsonFile([string]$json) {
    $script:jsonSeq++
    $p = Join-Path $jsonDir ("p$($script:jsonSeq).json")
    [System.IO.File]::WriteAllText($p, $json, $noBom)
    return "@$p"
}

function Wait-Server {
    for ($i = 0; $i -lt 60; $i++) {
        # 注意：不能写 `-o $null` —— PowerShell 会把它变成空参数，
        # curl 因缺少 -o 的值而把响应体打到 stdout，拿到的就不是状态码。
        # Windows 上丢弃输出的正确写法是 `-o NUL`。
        $code = (& curl.exe -s -o NUL -w "%{http_code}" "$base/health" 2>&1 | Out-String)
        if ($code -and $code.Trim() -eq '200') { return $true }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

function Invoke-Curl([string[]]$curlArgs) {
    $bodyFile = Join-Path $env:TEMP ("curlbody_" + [guid]::NewGuid().ToString('N') + ".txt")
    try {
        $code = (& curl.exe -s -o $bodyFile -w "%{http_code}" @curlArgs 2>&1 | Out-String)
        if ($null -eq $code) { $code = '' }
        $body = ''
        if (Test-Path $bodyFile) {
            $text = [System.IO.File]::ReadAllText($bodyFile, $noBom)
            if ($text) { $body = $text.Trim() }
        }
        return [pscustomobject]@{ code = $code.Trim(); body = $body }
    } finally {
        Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
    }
}

# 带 JSON 体的请求
function Invoke-Json([string]$method, [string]$url, [string]$json) {
    return Invoke-Curl @('-X', $method, $url,
        '-H', 'Content-Type: application/json; charset=utf-8',
        '--data-binary', (New-JsonFile $json))
}

function Json([string]$body) {
    if ([string]::IsNullOrWhiteSpace($body)) { return $null }
    return $body | ConvertFrom-Json
}

$script:pass = 0
$script:fail = 0

function Show([string]$label, $res, [string]$expect = '') {
    Write-Host ""
    Write-Host "### $label" -ForegroundColor Cyan
    Write-Host "HTTP $($res.code)"
    if ($res.body) { Write-Host $res.body }
    if ($expect) {
        if ($res.code -eq $expect) {
            Write-Host "  -> 符合预期 ($expect)" -ForegroundColor Green
            $script:pass++
        } else {
            Write-Host "  -> 不符预期：期望 $expect，实际 $($res.code)" -ForegroundColor Red
            $script:fail++
        }
    }
}

try {
    if (-not (Wait-Server)) {
        Write-Host "服务未就绪，日志：" -ForegroundColor Red
        Get-Content (Join-Path $tmp 'err.log') -ErrorAction SilentlyContinue | Select-Object -Last 30
        exit 1
    }
    Write-Host "服务已就绪" -ForegroundColor Green

    Show "1. GET /folders/tree（初始为空数组）" (Invoke-Curl @("$base/folders/tree")) '200'

    # --- 正常创建 ---
    Show "2. POST 建学科 高等数学" (Invoke-Json 'POST' "$base/folders" '{"name":"高等数学"}') '201'

    $r = Invoke-Json 'POST' "$base/folders" '{"name":"概率论","sort_order":5}'
    Show "3. POST 建学科 概率论" $r '201'
    $subj2 = (Json $r.body).id

    $r = Invoke-Json 'POST' "$base/folders" "{""name"":""极限与连续"",""parent_id"":$subj2}"
    Show "4. POST 建大类 极限与连续（挂在概率论下）" $r '201'
    $catId = (Json $r.body).id

    $r = Invoke-Json 'POST' "$base/folders" "{""name"":""导数与微分"",""parent_id"":$subj2}"
    Show "5. POST 建大类 导数与微分" $r '201'

    # --- 错误场景 ---
    Show "6. 同名学科 -> 409" (Invoke-Json 'POST' "$base/folders" '{"name":"概率论"}') '409'
    Show "7. 同级同名大类 -> 409" (Invoke-Json 'POST' "$base/folders" "{""name"":""极限与连续"",""parent_id"":$subj2}") '409'
    Show "8. 三级嵌套 -> 400" (Invoke-Json 'POST' "$base/folders" "{""name"":""三级"",""parent_id"":$catId}") '400'
    Show "9. 父不存在 -> 404" (Invoke-Json 'POST' "$base/folders" '{"name":"孤儿","parent_id":99999}') '404'
    Show "10. 空名 -> 422" (Invoke-Json 'POST' "$base/folders" '{"name":""}') '422'

    # --- 树结构 ---
    Show "11. GET /folders/tree（两级树）" (Invoke-Curl @("$base/folders/tree")) '200'

    # --- 重命名 / 排序 ---
    Show "12. PUT 重命名大类 -> 200" (Invoke-Json 'PUT' "$base/folders/$catId" '{"name":"极限、连续与洛必达"}') '200'
    Show "13. PUT 改成同级已有名字 -> 409" (Invoke-Json 'PUT' "$base/folders/$catId" '{"name":"导数与微分"}') '409'
    Show "14. PUT sort_order=-1 -> 200" (Invoke-Json 'PUT' "$base/folders/$catId" '{"sort_order":-1}') '200'
    Show "15. PUT 不存在的 id -> 404" (Invoke-Json 'PUT' "$base/folders/99999" '{"name":"x"}') '404'

    Show "16. GET /folders/tree（sort_order=-1 应排最前）" (Invoke-Curl @("$base/folders/tree")) '200'

    # --- 删除 ---
    Show "17. DELETE 大类 -> 200" (Invoke-Curl @('-X', 'DELETE', "$base/folders/$catId")) '200'
    Show "18. 重复 DELETE -> 404" (Invoke-Curl @('-X', 'DELETE', "$base/folders/$catId")) '404'
    Show "19. 删除后树（该大类应消失）" (Invoke-Curl @("$base/folders/tree")) '200'

    # --- 有题目时拒删（用 Python 直接插一道题，避免再开一个写接口）---
    Write-Host ""
    Write-Host "### 20. 学科下有题目时删除应被拒" -ForegroundColor Cyan
    $seed = Join-Path $tmp 'seed.py'
    [System.IO.File]::WriteAllText($seed, @"
import sys
sys.path.insert(0, r'$root')
from app.database import SessionLocal
from app.models import Folder, Question, LEVEL_CATEGORY
with SessionLocal() as db:
    cat = db.query(Folder).filter(Folder.level == LEVEL_CATEGORY, Folder.deleted_at.is_(None)).first()
    if cat is None:
        print('NO_CATEGORY'); sys.exit(1)
    db.add(Question(folder_id=cat.id, stem='测试题：求 lim(x->0) sinx/x'))
    db.commit()
    print(f'已在大类 {cat.id} ({cat.name}) 下插入 1 道题')
"@, $noBom)
    & $py $seed

    $tree = Json (Invoke-Curl @("$base/folders/tree")).body
    $subjId = ($tree | Where-Object { $_.name -eq '概率论' }).id
    Show "21. DELETE 有题目的学科 -> 409" (Invoke-Curl @('-X', 'DELETE', "$base/folders/$subjId")) '409'
    # 注意：不能写成 "$base/folders/$subjId?force=true" —— PowerShell 会把
    # `$subjId?` 当变量名解析，URL 变成 "=true"。用 --url-query 传参最稳。
    Show "22. DELETE force=true -> 200" (Invoke-Curl @('-X', 'DELETE', "$base/folders/$subjId", '--url-query', 'force=true')) '200'
    Show "23. 删除后树" (Invoke-Curl @("$base/folders/tree")) '200'

} finally {
    if ($proc -and -not $proc.HasExited) { $proc.Kill() }
    Write-Host ""
    Write-Host ('=' * 78)
    Write-Host "断言结果: 符合预期 $script:pass 项，不符 $script:fail 项" -ForegroundColor $(if ($script:fail -eq 0) { 'Green' } else { 'Red' })
    Write-Host "服务已停止，清理临时目录..."
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
