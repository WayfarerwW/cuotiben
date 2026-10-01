# review 接口端到端验证：真实启动 uvicorn，用 curl 打复习接口。
# 独立临时数据库，不污染 data/cuotiben.db。
#
# 需要"今日到期/逾期"的数据：先通过 HTTP 建题（走真实接口），再用 sqlite3
# 把 next_review_at 回拨成逾期 —— 时间无法通过接口伪造。
#
# 沿用 curl_folders.ps1 绕开的 Windows PowerShell 5.1 坑：
#   1. `curl` 是 Invoke-WebRequest 的别名 -> 用 Invoke-Curl 显式调 curl.exe
#   2. 非 ASCII 传给原生 exe 会按 ANSI 损坏 -> JSON 写临时文件用 `-d @file`
#   3. Get-Content -Encoding utf8 会加 BOM -> 用 .NET 读取
#   4. `-o $null` 会变成空参数 -> 丢弃输出写 `-o NUL`
#   5. `@(Json $r.body)` 会把 Json 当成"单个数组参数"，得到 1 个元素；
#      必须先把 Json 结果存进变量，或直接用 .Count / 专用 JsonCount 函数
#   6. Set-StrictMode 下访问不存在的属性会直接抛异常（本次靠它抓出真 bug：
#      变量里存的是 id 数值，却写了 $q_ov1.id）
# 本文件必须存为 UTF-8 with BOM。

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = 'C:\Users\yinxu\AppData\Local\Programs\Python\Python313\python.exe'
$tmp = Join-Path $env:TEMP ("cuotiben_rv_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$jsonDir = Join-Path $tmp 'json'
New-Item -ItemType Directory -Force -Path $jsonDir | Out-Null
$dbFile = Join-Path $tmp 'rv.db'

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()

$env:CUOTIBEN_DATABASE_URL = "sqlite:///" + ($dbFile -replace '\\', '/')
$env:CUOTIBEN_TIMEZONE = 'Asia/Shanghai'
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

# 取 JSON 数组长度：先存变量再取 .Count（见文件头第 5 条坑）
function JsonCount([string]$body) {
    $parsed = Json $body
    if ($null -eq $parsed) { return 0 }
    return @($parsed).Count
}

function Assert([string]$label, [bool]$ok, [string]$detail = '') {
    if ($ok) {
        Write-Host "  -> $label 符合预期 $(if ($detail) { "($detail)" })" -ForegroundColor Green
        $script:pass++
    } else {
        Write-Host "  -> $label 不符预期 $(if ($detail) { "($detail)" })" -ForegroundColor Red
        $script:fail++
    }
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

# 直接改库把某题回拨为逾期（时间无法通过接口伪造）
function Set-Overdue([int]$questionId, [int]$daysAgo) {
    $sql = @"
import sqlite3, sys
from datetime import datetime, timedelta, timezone
db = sqlite3.connect(r'$dbFile')
row = db.execute(
    "SELECT id FROM review_records WHERE question_id=? AND deleted_at IS NULL "
    "ORDER BY created_at DESC, id DESC LIMIT 1", ($questionId,)).fetchone()
if row is None:
    print('NO_RECORD'); sys.exit(1)
target = datetime.now(timezone.utc) - timedelta(days=$daysAgo)
db.execute("UPDATE review_records SET next_review_at=? WHERE id=?",
           (target.strftime('%Y-%m-%d %H:%M:%S.%f') + '+00:00', row[0]))
db.commit(); db.close()
print('OK')
"@
    $f = Join-Path $tmp "ov_$questionId.py"
    [System.IO.File]::WriteAllText($f, $sql, $noBom)
    & $py $f | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "回拨逾期失败: question_id=$questionId" }
}

try {
    if (-not (Wait-Server)) {
        Write-Host "服务未就绪：" -ForegroundColor Red
        Get-Content (Join-Path $tmp 'err.log') -ErrorAction SilentlyContinue | Select-Object -Last 30
        exit 1
    }
    Write-Host "服务已就绪" -ForegroundColor Green

    # ---------- 准备 ----------
    $r = Invoke-Json 'POST' "$base/folders" '{"name":"高等数学"}'
    $subj = (Json $r.body).id
    $r = Invoke-Json 'POST' "$base/folders" "{""name"":""极限与连续"",""parent_id"":$subj}"
    $cat = (Json $r.body).id
    Write-Host "已建文件夹: 学科=$subj 大类=$cat"

    # ---------- 初始状态 ----------
    Write-Host ""
    Write-Host "=== 初始状态 ===" -ForegroundColor Yellow
    $r = Invoke-Curl @("$base/review/today")
    Show "1. GET /review/today（应为空数组）" $r '200'
    Assert "返回空数组" ((JsonCount $r.body) -eq 0) "count=$(JsonCount $r.body)"

    $r = Invoke-Curl @("$base/review/count")
    Show "2. GET /review/count（应为 0）" $r '200'
    Assert "count=0" ((Json $r.body).count -eq 0) "count=$((Json $r.body).count)"

    # ---------- 新题不会立刻到期 ----------
    Write-Host ""
    Write-Host "=== 新建题目：3 天后才到期 ===" -ForegroundColor Yellow
    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"求 lim(x→0) sinx/x","answer":"1","tags":["极限"],"is_starred":true}')
    Show "3. POST /questions 建题" $r '201'
    $q1 = Json $r.body
    Assert "新题 interval_index=0" ($q1.interval_index -eq 0) "interval_index=$($q1.interval_index)"
    Assert "新题 next_review_at 已生成" ($null -ne $q1.next_review_at) "$($q1.next_review_at)"
    $nra = [datetime]::Parse($q1.next_review_at).ToUniversalTime()
    $d = ($nra - [datetime]::UtcNow).TotalDays
    Assert "首条记录为 now()+3 天" ($d -gt 2.95 -and $d -lt 3.05) ("{0:N4} 天" -f $d)

    $r = Invoke-Curl @("$base/review/today")
    Assert "新题不在今日队列（未到期）" ((JsonCount $r.body) -eq 0) "count=$(JsonCount $r.body)"

    # 再建几道，用于逾期场景（这些变量存的是 id 数值，不要再取 .id）
    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"逾期1天"}')
    $q_ov1 = (Json $r.body).id
    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"逾期20天重点","is_starred":true}')
    $q_ov20 = (Json $r.body).id
    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"逾期30天"}')
    $q_ov30 = (Json $r.body).id
    Assert "三道逾期题创建成功" ($q_ov1 -gt 0 -and $q_ov20 -gt 0 -and $q_ov30 -gt 0) `
        "ids=$q_ov1/$q_ov20/$q_ov30"

    # ---------- 打勾：任何时间都能打 ----------
    Write-Host ""
    Write-Host "=== 打勾（不校验待复习状态）===" -ForegroundColor Yellow
    $r = Invoke-Json 'POST' "$base/review/$($q1.id)/check" '{}'
    Show "4. POST /review/{id}/check（未到期的题也能打）" $r '200'
    $rec = Json $r.body
    Assert "打勾后 interval_index 重置为 0" ($rec.interval_index -eq 0) "interval_index=$($rec.interval_index)"
    Assert "打勾写入 last_review_at" ($null -ne $rec.last_review_at) "$($rec.last_review_at)"
    Assert "review_count=1" ($rec.review_count -eq 1) "review_count=$($rec.review_count)"
    $nra2 = [datetime]::Parse($rec.next_review_at).ToUniversalTime()
    Assert "next_review_at = now()+3 天" ((($nra2 - [datetime]::UtcNow).TotalDays) -gt 2.95) "$($rec.next_review_at)"

    Show "5. 重复打勾（不应 409）" (Invoke-Json 'POST' "$base/review/$($q1.id)/check" '{}') '200'
    $r = Invoke-Json 'POST' "$base/review/$($q1.id)/check" '{"mastery":2}'
    Show "6. 打勾带 mastery=2" $r '200'
    $rec6 = Json $r.body
    Assert "mastery_level=2 已记录" ($rec6.mastery_level -eq 2) "mastery_level=$($rec6.mastery_level)"
    Assert "mastery=2 仍重置 interval_index=0（取 5.2 口径）" ($rec6.interval_index -eq 0) "interval_index=$($rec6.interval_index)"
    Assert "review_count 累加到 3" ($rec6.review_count -eq 3) "review_count=$($rec6.review_count)"

    Show "7. 打勾不存在的题 -> 404" (Invoke-Json 'POST' "$base/review/99999/check" '{}') '404'
    Show "8. mastery 越界 -> 422" (Invoke-Json 'POST' "$base/review/$($q1.id)/check" '{"mastery":9}') '422'

    # ---------- 撤销 ----------
    Write-Host ""
    Write-Host "=== 撤销 ===" -ForegroundColor Yellow
    Show "9. POST /review/{id}/uncheck" (Invoke-Curl @('-X', 'POST', "$base/review/$($q1.id)/uncheck")) '200'
    $detail = Json (Invoke-Curl @("$base/questions/$($q1.id)")).body
    Assert "撤销后 review_count 回退到 2" ($detail.review_count -eq 2) "review_count=$($detail.review_count)"

    # 连撤到底：该题共 1 条首记录 + 3 次打勾 = 4 条，前面已撤 1 次，还需 3 次
    Invoke-Curl @('-X', 'POST', "$base/review/$($q1.id)/uncheck") | Out-Null
    Invoke-Curl @('-X', 'POST', "$base/review/$($q1.id)/uncheck") | Out-Null
    Invoke-Curl @('-X', 'POST', "$base/review/$($q1.id)/uncheck") | Out-Null
    Show "10. 撤到底后再撤 -> 409" (Invoke-Curl @('-X', 'POST', "$base/review/$($q1.id)/uncheck")) '409'
    Show "11. 撤销不存在的题 -> 404" (Invoke-Curl @('-X', 'POST', "$base/review/99999/uncheck")) '404'

    # ---------- 今日队列与补卡 ----------
    Write-Host ""
    Write-Host "=== 今日队列与补卡 ===" -ForegroundColor Yellow
    Set-Overdue $q_ov1 1
    Set-Overdue $q_ov20 20
    Set-Overdue $q_ov30 30

    $r = Invoke-Curl @("$base/review/today")
    Show "12. GET /review/today（含逾期题）" $r '200'
    $itemsParsed = Json $r.body
    $itemsArr = @($itemsParsed)
    Assert "队列含 3 道逾期题" ($itemsArr.Count -eq 3) "count=$($itemsArr.Count)"
    $fields = @($itemsArr[0].PSObject.Properties.Name)
    foreach ($f in @('question_id', 'stem', 'answer', 'images', 'tags',
                     'folder_name', 'is_starred', 'interval_index', 'next_review_at')) {
        Assert "队列项含字段 $f" ($fields -contains $f) "字段=$($fields -join ',')"
    }
    Assert "重点题排在最前" ($itemsArr[0].question_id -eq $q_ov20) "首项=$($itemsArr[0].stem)"
    $it20 = $itemsArr | Where-Object { $_.question_id -eq $q_ov20 }
    $it30 = $itemsArr | Where-Object { $_.question_id -eq $q_ov30 }
    $it1 = $itemsArr | Where-Object { $_.question_id -eq $q_ov1 }
    Assert "逾期天数正确" (($it20.overdue_days -eq 20) -and ($it30.overdue_days -eq 30)) `
        "20天=$($it20.overdue_days) 30天=$($it30.overdue_days)"
    Assert "逾期>=14 天标记积压、1 天不标记" `
        (($it20.is_backlog -eq $true) -and ($it1.is_backlog -eq $false)) "is_backlog"
    Assert "folder_name 已填充" `
        ((@($itemsArr | Where-Object { $_.folder_name -eq '极限与连续' })).Count -eq 3) "folder_name"

    $r = Invoke-Curl @("$base/review/count")
    Show "13. GET /review/count" $r '200'
    Assert "count 与队列长度一致" ((Json $r.body).count -eq 3) "count=$((Json $r.body).count)"

    # 打勾后离开队列。
    # 注意：严格模式下 $arr.prop 的成员枚举不可靠（单元素数组会抛
    # PropertyNotFoundStrict），所以先投影出 id 列表再比较。
    Invoke-Json 'POST' "$base/review/$q_ov1/check" '{}' | Out-Null
    $afterParsed = Json (Invoke-Curl @("$base/review/today")).body
    $afterIds = @(@($afterParsed) | ForEach-Object { $_.question_id })
    $afterStems = @(@($afterParsed) | ForEach-Object { $_.stem })
    Assert "打勾后离开队列" ($afterIds -notcontains $q_ov1) `
        "剩余=$($afterStems -join ',')"
    Assert "count 随之下降" ((Json (Invoke-Curl @("$base/review/count")).body).count -eq 2) "count=2"

    # 撤销后回到队列
    Invoke-Curl @('-X', 'POST', "$base/review/$q_ov1/uncheck") | Out-Null
    $backParsed = Json (Invoke-Curl @("$base/review/today")).body
    $backIds = @(@($backParsed) | ForEach-Object { $_.question_id })
    Assert "撤销后回到队列（上一轮自动生效）" ($backIds -contains $q_ov1) `
        "count=$($backIds.Count)"

    # ---------- 补卡统计 ----------
    Write-Host ""
    Write-Host "=== 补卡统计 ===" -ForegroundColor Yellow
    # 造一道确定逾期的题并打勾，避免依赖前面已被撤销的题：
    # 撤销会软删除那条补卡记录，统计按软删除语义本就该排除它（正确行为）。
    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"补卡统计用题"}')
    $q_stat = (Json $r.body).id
    Set-Overdue $q_stat 20

    $statsBefore2 = Json (Invoke-Curl @("$base/review/backfill/stats")).body
    Assert "stats 含 today_backfill_count / consecutive_days / backlog_count" (
        (@($statsBefore2.PSObject.Properties.Name) -contains 'today_backfill_count') -and
        (@($statsBefore2.PSObject.Properties.Name) -contains 'consecutive_days') -and
        (@($statsBefore2.PSObject.Properties.Name) -contains 'backlog_count')) `
        "字段=$(@($statsBefore2.PSObject.Properties.Name) -join ',')"

    Invoke-Json 'POST' "$base/review/$q_stat/check" '{}' | Out-Null
    $r = Invoke-Curl @("$base/review/backfill/stats")
    Show "14. GET /review/backfill/stats（补卡后）" $r '200'
    $st = Json $r.body
    Assert "对逾期题打勾后今日补卡数增加" `
        ($st.today_backfill_count -gt $statsBefore2.today_backfill_count) `
        "$($statsBefore2.today_backfill_count) -> $($st.today_backfill_count)"
    Assert "连续补卡天数 = 1（只有今天）" ($st.consecutive_days -eq 1) `
        "consecutive_days=$($st.consecutive_days)"

    # 撤销该次补卡后，统计应排除它（软删除语义）
    Invoke-Curl @('-X', 'POST', "$base/review/$q_stat/uncheck") | Out-Null
    $stAfterUncheck = Json (Invoke-Curl @("$base/review/backfill/stats")).body
    Assert "撤销补卡后今日补卡数回落（软删除即不计）" `
        ($stAfterUncheck.today_backfill_count -lt $st.today_backfill_count) `
        "$($st.today_backfill_count) -> $($stAfterUncheck.today_backfill_count)"

    # ---------- 一键重置积压：全部重置 ----------
    Write-Host ""
    Write-Host "=== 一键重置积压 ===" -ForegroundColor Yellow
    # 先补足积压题：再建两道逾期 20 天
    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"积压A","is_starred":true}')
    $bg1 = (Json $r.body).id
    $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"积压B"}')
    $bg2 = (Json $r.body).id
    Set-Overdue $bg1 20
    Set-Overdue $bg2 25

    $statsBefore = Json (Invoke-Curl @("$base/review/backfill/stats")).body
    Assert "重置前积压数 >= 3（ov20/ov30 仍在积压）" `
        ($statsBefore.backlog_count -ge 3) "backlog_count=$($statsBefore.backlog_count)"

    $r = Invoke-Curl @('-X', 'POST', "$base/review/backfill/reset")
    Show "15. POST /review/backfill/reset（全部重置，无 body）" $r '200'
    $reset = Json $r.body
    Assert "返回 affected_count" (@($reset.PSObject.Properties.Name) -contains 'affected_count') `
        "affected_count=$($reset.affected_count)"
    Assert "affected_count = 重置前积压数" `
        ($reset.affected_count -eq $statsBefore.backlog_count) `
        "$($reset.affected_count) vs $($statsBefore.backlog_count)"
    Assert "mode=all" ($reset.mode -eq 'all') "mode=$($reset.mode)"

    $statsAfter = Json (Invoke-Curl @("$base/review/backfill/stats")).body
    Assert "重置后积压数归零" ($statsAfter.backlog_count -eq 0) `
        "backlog_count=$($statsAfter.backlog_count)"
    Assert "重置计入今日补卡数" `
        ($statsAfter.today_backfill_count -gt $statsBefore.today_backfill_count) `
        "$($statsBefore.today_backfill_count) -> $($statsAfter.today_backfill_count)"

    # 逐题核对 interval_index 与 next_review_at
    $detailBg = Json (Invoke-Curl @("$base/questions/$bg1")).body
    Assert "重置后 interval_index=0" ($detailBg.interval_index -eq 0) `
        "interval_index=$($detailBg.interval_index)"
    $bgDue = [datetime]::Parse($detailBg.next_review_at).ToUniversalTime()
    $bgDays = ($bgDue - [datetime]::UtcNow).TotalDays
    Assert "重置后 next_review_at = now()+3 天" ($bgDays -gt 2.95 -and $bgDays -lt 3.05) `
        ("{0:N4} 天" -f $bgDays)

    # ---------- 一键重置积压：分散到未来 N 天 ----------
    Write-Host ""
    Write-Host "=== 分散重置到未来 N 天 ===" -ForegroundColor Yellow
    $spreadIds = @()
    for ($i = 0; $i -lt 4; $i++) {
        $r = Invoke-Json 'POST' "$base/questions" ('{"folder_id":' + $cat + ',"stem":"分散' + $i + '"}')
        $qid = (Json $r.body).id
        Set-Overdue $qid (20 + $i)
        $spreadIds += $qid
    }
    Assert "已造 4 道积压题" ($spreadIds.Count -eq 4) "ids=$($spreadIds -join ',')"

    $r = Invoke-Json 'POST' "$base/review/backfill/reset" '{"spread":true,"days":3}'
    Show "16. POST /review/backfill/reset（spread=true, days=3）" $r '200'
    $reset2 = Json $r.body
    Assert "mode=spread 且 days 回显" `
        (($reset2.mode -eq 'spread') -and ($reset2.spread_days -eq 3)) `
        "mode=$($reset2.mode) days=$($reset2.spread_days)"
    Assert "affected_count=4" ($reset2.affected_count -eq 4) "affected=$($reset2.affected_count)"

    $offsets = @()
    foreach ($qid in $spreadIds) {
        $dd = Json (Invoke-Curl @("$base/questions/$qid")).body
        $dDays = ([datetime]::Parse($dd.next_review_at).ToUniversalTime() -
                  [datetime]::UtcNow).TotalDays
        $offsets += [math]::Round($dDays, 3)
    }
    $offsets = $offsets | Sort-Object
    Assert "分散后到期时间互不相同" `
        ((@($offsets | Select-Object -Unique)).Count -eq 4) "offsets=$($offsets -join ',')"
    Assert "分散窗口落在 3~6 天内（base 3 + 0~3 天错开）" `
        (($offsets[0] -gt 2.9) -and ($offsets[-1] -lt 6.1)) "最早=$($offsets[0]) 最晚=$($offsets[-1])"

    Assert "重置后积压数归零" `
        ((Json (Invoke-Curl @("$base/review/backfill/stats")).body).backlog_count -eq 0) "0"

    # 无积压时重置不报错
    $r = Invoke-Curl @('-X', 'POST', "$base/review/backfill/reset")
    Show "17. 无积压时重置（应 200 且 affected_count=0）" $r '200'
    Assert "affected_count=0" ((Json $r.body).affected_count -eq 0) `
        "affected=$((Json $r.body).affected_count)"

    # days 越界 -> 422
    Show "18. days=0 越界 -> 422" (Invoke-Json 'POST' "$base/review/backfill/reset" '{"spread":true,"days":0}') '422'

} finally {
    if ($proc -and -not $proc.HasExited) { $proc.Kill() }
    Write-Host ""
    Write-Host ('=' * 78)
    Write-Host "断言结果: 符合预期 $script:pass 项，不符 $script:fail 项" -ForegroundColor $(if ($script:fail -eq 0) { 'Green' } else { 'Red' })
    Write-Host "服务已停止，清理临时目录..."
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
