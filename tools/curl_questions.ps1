# questions 接口端到端验证：真实启动 uvicorn，用 curl 打全部题目接口。
# 使用独立临时数据库，不污染 data/cuotiben.db。
#
# 沿用 curl_folders.ps1 绕开的 Windows PowerShell 5.1 坑：
#   1. `curl` 是 Invoke-WebRequest 的别名 -> 用 Invoke-Curl 显式调 curl.exe
#   2. 非 ASCII 作为命令行参数传给原生 exe 会被按 ANSI 代码页损坏
#      -> JSON 载荷写临时文件，用 `-d @file`
#   3. Get-Content -Encoding utf8 会加 BOM 导致 ConvertFrom-Json 失败 -> 用 .NET 读
#   4. `-o $null` 会变成空参数 -> 丢弃输出要写 `-o NUL`
# 本文件必须存为 UTF-8 with BOM。

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = 'C:\Users\yinxu\AppData\Local\Programs\Python\Python313\python.exe'
$tmp = Join-Path $env:TEMP ("cuotiben_q_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$jsonDir = Join-Path $tmp 'json'
New-Item -ItemType Directory -Force -Path $jsonDir | Out-Null
$dbFile = Join-Path $tmp 'q.db'

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
$script:pass = 0
$script:fail = 0

function New-JsonFile([string]$json) {
    $script:jsonSeq++
    $p = Join-Path $jsonDir ("p$($script:jsonSeq).json")
    [System.IO.File]::WriteAllText($p, $json, $noBom)
    return "@$p"
}

function Wait-Server {
    for ($i = 0; $i -lt 60; $i++) {
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

function Invoke-Json([string]$method, [string]$url, [string]$json) {
    return Invoke-Curl @('-X', $method, $url,
        '-H', 'Content-Type: application/json; charset=utf-8',
        '--data-binary', (New-JsonFile $json))
}

function Json([string]$body) {
    if ([string]::IsNullOrWhiteSpace($body)) { return $null }
    return $body | ConvertFrom-Json
}

function Show([string]$label, $res, [string]$expect = '') {
    Write-Host ""
    Write-Host "### $label" -ForegroundColor Cyan
    Write-Host "HTTP $($res.code)"
    if ($res.body) { Write-Host $res.body }
    if ($expect) {
        if ($res.code -eq $expect) {
            Write-Host "  -> 符合预期 ($expect)" -ForegroundColor Green; $script:pass++
        } else {
            Write-Host "  -> 不符预期：期望 $expect，实际 $($res.code)" -ForegroundColor Red; $script:fail++
        }
    }
}

function Assert([string]$label, [bool]$ok, [string]$detail = '') {
    if ($ok) {
        Write-Host "  -> $label 符合预期 $(if ($detail) { "($detail)" })" -ForegroundColor Green; $script:pass++
    } else {
        Write-Host "  -> $label 不符预期 $(if ($detail) { "($detail)" })" -ForegroundColor Red; $script:fail++
    }
}

try {
    if (-not (Wait-Server)) {
        Write-Host "服务未就绪：" -ForegroundColor Red
        Get-Content (Join-Path $tmp 'err.log') -ErrorAction SilentlyContinue | Select-Object -Last 30
        exit 1
    }
    Write-Host "服务已就绪" -ForegroundColor Green

    # ---------- 准备文件夹 ----------
    $r = Invoke-Json 'POST' "$base/folders" '{"name":"高等数学"}'
    $subj = (Json $r.body).id
    $r = Invoke-Json 'POST' "$base/folders" "{""name"":""极限与连续"",""parent_id"":$subj}"
    $cat = (Json $r.body).id
    $r = Invoke-Json 'POST' "$base/folders" "{""name"":""导数与微分"",""parent_id"":$subj}"
    $cat2 = (Json $r.body).id
    Write-Host "已建文件夹: 学科=$subj 大类=$cat / $cat2"

    # ---------- 创建题目 ----------
    Write-Host ""
    Write-Host "=== 创建题目 ===" -ForegroundColor Yellow
    $body = '{"folder_id":' + $cat + ',"stem":"求 lim(x→0) sinx/x","answer":"1","tags":["极限"," 极限 ","ＡＢＣ","等价无穷小"],"is_starred":true,"images":["uploads/2026/10/01/a.jpg"]}'
    $r = Invoke-Json 'POST' "$base/questions" $body
    Show "1. POST /questions（含标签归一化与图片）" $r '201'
    $q1 = Json $r.body
    Assert "标签归一化去重后为 3 个（极限/abc/等价无穷小）" ($q1.tags.Count -eq 3) "tags=$($q1.tags.name -join ',')"
    Assert "全角 ＡＢＣ 归一化为 abc" (@($q1.tags.name) -contains 'abc') "tags=$($q1.tags.name -join ',')"
    Assert "images 已关联" (@($q1.images).Count -eq 1) "images=$(@($q1.images.file_path) -join ',')"
    Assert "next_review_at 已生成" ($null -ne $q1.next_review_at) "next_review_at=$($q1.next_review_at)"
    Assert "interval_index=0" ($q1.interval_index -eq 0) "interval_index=$($q1.interval_index)"
    Assert "review_count=0" ($q1.review_count -eq 0) "review_count=$($q1.review_count)"
    Assert "folder_name 已返回" ($q1.folder_name -eq '极限与连续') "folder_name=$($q1.folder_name)"

    Write-Host ""
    Write-Host "### 1b. next_review_at 是否为 now()+3 天" -ForegroundColor Cyan
    $nra = [datetime]::Parse($q1.next_review_at).ToUniversalTime()
    $days = ($nra - [datetime]::UtcNow).TotalDays
    Assert "距当前约 3 天" ($days -gt 2.95 -and $days -lt 3.05) ("{0:N4} 天" -f $days)

    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"第二题","tags":["极限","洛必达"]}')
    Show "2. POST /questions（复用已有标签 极限）" $r '201'
    $q2 = Json $r.body

    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat2 + ',"stem":"导数定义","answer":"极限的另一种形式","tags":["导数"]}')
    Show "3. POST /questions（另一个大类）" $r '201'
    $q3 = Json $r.body

    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat2 + ',"stem":"洛必达法则","tags":["极限","洛必达"],"mastery_status":"mastered"}')
    Show "4. POST /questions（已拿下）" $r '201'
    $q4 = Json $r.body

    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"images":["uploads/only-image.jpg"]}')
    Show "5. POST /questions（纯图片题，题干答案都空）" $r '201'

    # ---------- 错误场景 ----------
    Write-Host ""
    Write-Host "=== 错误场景 ===" -ForegroundColor Yellow
    Show "6. 挂到一级学科 -> 400" (Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $subj + ',"stem":"x"}')) '400'
    Show "7. folder 不存在 -> 404" (Invoke-Json 'POST' "$base/questions" '{"folder_id":99999,"stem":"x"}') '404'
    Show "8. 缺 folder_id -> 422" (Invoke-Json 'POST' "$base/questions" '{"stem":"x"}') '422'
    Show "9. 非法 mastery_status -> 422" (Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"mastery_status":"bogus"}')) '422'

    # ---------- 列表与筛选 ----------
    Write-Host ""
    Write-Host "=== 列表筛选 ===" -ForegroundColor Yellow
    $r = Invoke-Curl @("$base/questions")
    Show "10. GET /questions（全部）" $r '200'
    $all = Json $r.body
    Assert "返回 5 条" (@($all).Count -eq 5) "count=$(@($all).Count)"
    Assert "含约定字段 id/stem/answer/is_starred/mastery_status/tags/folder_name" `
        ((@($all[0].PSObject.Properties.Name) -contains 'id') -and
         (@($all[0].PSObject.Properties.Name) -contains 'stem') -and
         (@($all[0].PSObject.Properties.Name) -contains 'answer') -and
         (@($all[0].PSObject.Properties.Name) -contains 'is_starred') -and
         (@($all[0].PSObject.Properties.Name) -contains 'mastery_status') -and
         (@($all[0].PSObject.Properties.Name) -contains 'tags') -and
         (@($all[0].PSObject.Properties.Name) -contains 'folder_name')) `
        "字段=$(@($all[0].PSObject.Properties.Name) -join ',')"

    Show "11. 按 folder_id 筛选" (Invoke-Curl @("$base/questions", '--url-query', "folder_id=$cat2")) '200'
    $byFolder = Json (Invoke-Curl @("$base/questions", '--url-query', "folder_id=$cat2")).body
    Assert "命中 2 条" (@($byFolder).Count -eq 2) "count=$(@($byFolder).Count)"

    Show "12. 多标签 AND（极限+洛必达）" (Invoke-Curl @("$base/questions", '--url-query', 'tag=极限', '--url-query', 'tag=洛必达', '--url-query', 'tag_mode=and')) '200'
    $andQ = Json (Invoke-Curl @("$base/questions", '--url-query', 'tag=极限', '--url-query', 'tag=洛必达', '--url-query', 'tag_mode=and')).body
    Assert "AND 命中 2 条（第二题/洛必达法则）" (@($andQ).Count -eq 2) "stems=$(@($andQ.stem) -join ' | ')"

    Show "13. 多标签 OR（极限 or 导数）" (Invoke-Curl @("$base/questions", '--url-query', 'tag=极限', '--url-query', 'tag=导数', '--url-query', 'tag_mode=or')) '200'
    $orQ = Json (Invoke-Curl @("$base/questions", '--url-query', 'tag=极限', '--url-query', 'tag=导数', '--url-query', 'tag_mode=or')).body
    Assert "OR 命中 4 条" (@($orQ).Count -eq 4) "count=$(@($orQ).Count)"

    Show "14. keyword 搜索题干" (Invoke-Curl @("$base/questions", '--url-query', 'keyword=洛必达')) '200'
    $kw = Json (Invoke-Curl @("$base/questions", '--url-query', 'keyword=洛必达')).body
    Assert "命中 1 条" (@($kw).Count -eq 1) "stems=$(@($kw.stem) -join ' | ')"

    Show "15. keyword 搜索答案" (Invoke-Curl @("$base/questions", '--url-query', 'keyword=另一种形式')) '200'
    $kw2 = Json (Invoke-Curl @("$base/questions", '--url-query', 'keyword=另一种形式')).body
    Assert "命中 1 条（导数定义）" (@($kw2).Count -eq 1) "stems=$(@($kw2.stem) -join ' | ')"

    Show "16. starred=true" (Invoke-Curl @("$base/questions", '--url-query', 'starred=true')) '200'
    $st = Json (Invoke-Curl @("$base/questions", '--url-query', 'starred=true')).body
    Assert "命中 1 条" (@($st).Count -eq 1) "count=$(@($st).Count)"

    Show "17. mastery=mastered" (Invoke-Curl @("$base/questions", '--url-query', 'mastery=mastered')) '200'
    $ms = Json (Invoke-Curl @("$base/questions", '--url-query', 'mastery=mastered')).body
    Assert "命中 1 条" (@($ms).Count -eq 1) "count=$(@($ms).Count)"

    Show "18. tag_mode 非法值 -> 422" (Invoke-Curl @("$base/questions", '--url-query', 'tag_mode=xor')) '422'

    # ---------- 详情 ----------
    Write-Host ""
    Write-Host "=== 详情 / 编辑 ===" -ForegroundColor Yellow
    $r = Invoke-Curl @("$base/question/$($q1.id)")
    Show "19. GET /question/{id}（详情）" $r '200'
    $detail = Json $r.body
    Assert "详情含 tags 与 images" ((@($detail.tags).Count -eq 3) -and (@($detail.images).Count -eq 1)) `
        "tags=$(@($detail.tags.name) -join ',') images=$(@($detail.images).Count)"
    Show "20. GET 不存在的题目 -> 404" (Invoke-Curl @("$base/question/99999")) '404'

    $r = Invoke-Json 'PUT' "$base/questions/$($q1.id)" '{"stem":"改过的题干","tags":["新标签"]}'
    Show "21. PUT /questions/{id} 编辑" $r '200'
    $upd = Json $r.body
    Assert "题干已改且标签整体替换" (($upd.stem -eq '改过的题干') -and (@($upd.tags).Count -eq 1) -and ($upd.tags[0].name -eq '新标签')) `
        "stem=$($upd.stem) tags=$(@($upd.tags.name) -join ',')"
    Assert "未传的图片字段保持不变" (@($upd.images).Count -eq 1) "images=$(@($upd.images).Count)"
    Show "22. PUT 不存在 -> 404" (Invoke-Json 'PUT' "$base/questions/99999" '{"stem":"x"}') '404'
    Show "23. PUT 改挂到一级学科 -> 400" (Invoke-Json 'PUT' "$base/questions/$($q1.id)" ('{"folder_id":' + $subj + '}')) '400'

    # ---------- star / mastery ----------
    Write-Host ""
    Write-Host "=== 重点与正误 ===" -ForegroundColor Yellow
    $r = Invoke-Curl @('-X', 'POST', "$base/questions/$($q2.id)/star")
    Show "24. POST /questions/{id}/star" $r '200'
    Assert "is_starred=true" ((Json $r.body).is_starred -eq $true)

    $r = Invoke-Curl @('-X', 'POST', "$base/questions/$($q2.id)/unstar")
    Show "25. POST /questions/{id}/unstar" $r '200'
    Assert "is_starred=false" ((Json $r.body).is_starred -eq $false)

    $r = Invoke-Curl @('-X', 'POST', "$base/questions/$($q3.id)/mastery")
    Show "26. POST /questions/{id}/mastery（不传体，翻转）" $r '200'
    Assert "still_wrong -> mastered" ((Json $r.body).mastery_status -eq 'mastered') "mastery=$((Json $r.body).mastery_status)"

    $r = Invoke-Json 'POST' "$base/questions/$($q3.id)/mastery" '{"mastery_status":"still_wrong"}'
    Show "27. mastery 指定目标状态" $r '200'
    Assert "mastered -> still_wrong" ((Json $r.body).mastery_status -eq 'still_wrong') "mastery=$((Json $r.body).mastery_status)"

    Show "28. star 不存在的题 -> 404" (Invoke-Curl @('-X', 'POST', "$base/questions/99999/star")) '404'

    # ---------- 软删除 ----------
    Write-Host ""
    Write-Host "=== 软删除 ===" -ForegroundColor Yellow
    Show "29. DELETE /questions/{id}" (Invoke-Curl @('-X', 'DELETE', "$base/questions/$($q3.id)")) '200'
    $after = Json (Invoke-Curl @("$base/questions")).body
    Assert "删除后剩 4 条" (@($after).Count -eq 4) "count=$(@($after).Count)"
    Assert "被删题目不在列表中" (@($after.id) -notcontains $q3.id) "ids=$(@($after.id) -join ',')"
    Show "30. 删除后再取详情 -> 404" (Invoke-Curl @("$base/question/$($q3.id)")) '404'
    Show "31. 重复删除 -> 404" (Invoke-Curl @('-X', 'DELETE', "$base/questions/$($q3.id)")) '404'

} finally {
    if ($proc -and -not $proc.HasExited) { $proc.Kill() }
    Write-Host ""
    Write-Host ('=' * 78)
    Write-Host "断言结果: 符合预期 $script:pass 项，不符 $script:fail 项" -ForegroundColor $(if ($script:fail -eq 0) { 'Green' } else { 'Red' })
    Write-Host "服务已停止，清理临时目录..."
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
