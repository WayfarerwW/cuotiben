"""UI 验证用服务器：静态前端 + 后端 API + 测试页，同一 origin。

*** 这是测试脚手架，不是生产入口。***

生产启动方式是 `python run.py`（它调用 `app/main.py`，由后者挂载
`/static` 与 `/uploads` 并提供根路径）。本文件之所以存在，是因为
`tools/verify_ui.py` 需要在**同一个 origin** 下同时访问前端页面、后端 API
与注入的测试页 —— 跨 origin（file:// 对 http://）时浏览器不允许脚本
读取 iframe 内的 DOM，测试无法进行。

因此本文件只做三件事：
  1. 按前缀把请求分流给 FastAPI 或静态文件（等价于生产环境的同源部署）
  2. 提供 `/__harness` 返回测试页
  3. 提供 `/__redue`、`/__refill` 两个**仅测试用**接口，把复习记录的到期
     时间回拨/重造（时间无法通过接口伪造，只能直接改临时库）

它不会写入 data/cuotiben.db —— 库路径由调用方以参数传入临时文件。

用法：python tools/_serve_for_ui_test.py <port> <harness.html 路径> [db 路径]
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    port = int(sys.argv[1])
    harness_path = Path(sys.argv[2])
    db_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    if db_path is not None:
        os.environ["CUOTIBEN_DATABASE_URL"] = "sqlite:///" + db_path.as_posix()

    from starlette.responses import FileResponse, Response

    from app.main import app as api_app

    STATIC = ROOT / "app/static"
    API_PREFIXES = (
        "/folders", "/questions", "/review", "/tags", "/notes",
        "/settings", "/upload", "/export", "/data", "/health",
    )
    MIME = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }

    LOG = os.environ.get("UI_TEST_LOG") == "1"

    # 截图辅助页：把被测页面放进 iframe，加载后自动点开通知面板并展开第一项
    notif_shot = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>notif-shot</title><style>html,body{margin:0;padding:0;overflow:hidden}</style></head>
<body><script>
const wait = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, timeout) {
  const end = Date.now() + (timeout || 8000);
  while (Date.now() < end) {
    try { const v = fn(); if (v) return v; } catch (e) {}
    await wait(150);
  }
  return null;
}
(async () => {
  // 先把队列填满（测试跑完会把队列打勾打空）
  try { await fetch('/__refill', { method: 'POST' }); } catch (e) {}

  const f = document.createElement('iframe');
  f.style.cssText = 'width:1440px;height:900px;border:0';
  f.src = '/';
  document.body.appendChild(f);

  const bell = await until(() => f.contentDocument.getElementById('btn-bell'), 12000);
  if (!bell) return;
  await wait(1200);           // 等首次 loadReview 完成
  bell.click();
  await until(() => f.contentDocument.querySelector('.notif__item'), 6000);

  const head = f.contentDocument.querySelector('.notif__item .notif__item-head');
  if (head) head.click();
  await wait(700);
  const ans = await until(() => Array.from(
    f.contentDocument.querySelectorAll('.notif__review button'))
    .find(b => b.textContent.includes('展开答案')), 5000);
  if (ans) ans.click();
})();
</script></body></html>"""

    import sqlite3
    from datetime import UTC, datetime, timedelta

    def force_due() -> int:
        """把复习记录回拨成逾期/今日到期（仅测试用）。

        时间无法通过接口伪造，所以直接改库；只动传入的临时库。
        """
        if not db_path:
            return 0
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            rows = cur.execute(
                "SELECT id FROM review_records WHERE deleted_at IS NULL "
                "ORDER BY question_id"
            ).fetchall()
            offsets = [5, 20, 0]
            now = datetime.now(UTC)
            for i, (rid,) in enumerate(rows):
                off = offsets[i] if i < len(offsets) else 0
                cur.execute(
                    "UPDATE review_records SET next_review_at=? WHERE id=?",
                    ((now - timedelta(days=off, hours=1)).isoformat(), rid),
                )
            con.commit()
            return len(rows)
        finally:
            con.close()

    def recreate_due() -> int:
        """给每道未删除的题新插一条"昨天到期"的复习记录。

        验证跑完会把队列打勾打空，导致通知面板截图没有内容；
        这个接口用来把队列重新填满（仅测试用）。
        """
        if not db_path:
            return 0
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.cursor()
            qids = [r[0] for r in cur.execute(
                "SELECT id FROM questions WHERE deleted_at IS NULL ORDER BY id"
            ).fetchall()]
            now = datetime.now(UTC)
            yesterday = (now - timedelta(days=1)).isoformat()
            for qid in qids:
                cur.execute(
                    "INSERT INTO review_records (question_id, review_count, "
                    "interval_index, last_review_at, next_review_at, mastery_level, "
                    "is_backfill, created_at) VALUES (?, 1, 0, ?, ?, 0, 0, ?)",
                    (qid, yesterday, yesterday, now.isoformat()),
                )
            con.commit()
            return len(qids)
        finally:
            con.close()

    async def dispatch(scope, receive, send):
        if scope["type"] != "http":
            await api_app(scope, receive, send)
            return

        path = scope["path"]
        if LOG:
            print(f"[dispatch] {scope['method']} {path}", flush=True)

        # 仅测试用：让浏览器能在中途把队列重新变回待复习
        if path == "/__redue":
            n = force_due()
            await Response(f'{{"redue":{n}}}', media_type="application/json")(
                scope, receive, send)
            return

        # 仅测试用：把队列彻底填满（截图用）
        if path == "/__refill":
            n = recreate_due()
            await Response(f'{{"refill":{n}}}', media_type="application/json")(
                scope, receive, send)
            return

        if path == "/__harness":
            await FileResponse(harness_path, media_type=MIME[".html"])(scope, receive, send)
            return

        # 供截图用：加载首页并自动点开通知面板（面板内嵌复习视图）
        if path == "/__notif":
            await Response(notif_shot, media_type=MIME[".html"])(scope, receive, send)
            return

        if path.startswith(API_PREFIXES) or path in (
            "/openapi.json", "/docs", "/redoc",
        ):
            await api_app(scope, receive, send)
            return

        if path == "/favicon.ico":
            await Response(status_code=204)(scope, receive, send)
            return

        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        candidate = (STATIC / rel).resolve()
        try:
            candidate.relative_to(STATIC.resolve())
        except ValueError:
            candidate = None

        if candidate and candidate.is_file():
            await FileResponse(candidate,
                               media_type=MIME.get(candidate.suffix.lower(),
                                                   "application/octet-stream")
                               )(scope, receive, send)
            return

        await FileResponse(STATIC / "index.html", media_type=MIME[".html"])(scope, receive, send)

    import uvicorn

    uvicorn.run(dispatch, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
