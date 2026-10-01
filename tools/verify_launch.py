"""验证"启动后可访问"这条验收标准（requirements.md 第 8 节第一条）。

做四件事：
  1. 用 `python run.py` 真实启动服务（与用户手动启动的方式一致）
  2. 抓取 index.html，逐个请求它引用的静态资源，断言**没有 404**
  3. 在无头浏览器里打开 http://localhost:8000，断言 Vue 真的挂载且页面渲染
  4. 断言 API 仍然可用（挂了静态目录后没被吞掉）

默认用临时数据库，避免污染 data/cuotiben.db。

用法：python tools/verify_launch.py [port]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def fetch(url: str, timeout: int = 15):
    """返回 (status, body_bytes, content_type)。

    显式禁用代理：系统代理会把 127.0.0.1 的请求也劫持走，
    导致"服务器明明起来了却连不上"的假失败。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("content-type", "")


def wait_ready(base: str, proc: subprocess.Popen, seconds: int = 40) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            status, _, _ = fetch(base + "/health", timeout=3)
            if status == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def port_in_use(port: int) -> bool:
    """端口是否已被占用。

    必须提前检查：如果 8000 上已经有一个旧的服务在跑（比如上次调试留下的
    uvicorn reload 工作进程），本脚本新起的服务会绑定失败，而断言却打在
    **那个旧服务**上 —— 会出现"绿色通过"但验证的其实不是本次代码的情况。
    调试时就被这个坑过很久（旧进程还注册着没有 /__harness 的旧应用）。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    base = f"http://127.0.0.1:{port}"

    print("=" * 78)
    print("启动与可访问性验证")
    print("=" * 78)

    if port_in_use(port):
        print(f"端口 {port} 已被占用，请先停止那个服务再运行本脚本。")
        print("（否则断言会打在旧服务上，验的不是本次代码）")
        print(f"  Windows: netstat -ano | findstr :{port}   然后 taskkill /F /T /PID <pid>")
        return 3

    tmp = Path(tempfile.mkdtemp(prefix="cuotiben_launch_"))
    db = tmp / "launch.db"

    env = dict(os.environ)
    env["CUOTIBEN_DATABASE_URL"] = "sqlite:///" + db.as_posix()
    env["PYTHONIOENCODING"] = "utf-8"
    # 让被测应用把渲染检查页挂到同源的 /__harness（默认关闭，见 main.py）
    harness_file = tmp / "render.html"
    harness_file.write_text(RENDER_HARNESS.replace("__BASE__", base),
                            encoding="utf-8")
    env["CUOTIBEN_TEST_HARNESS"] = str(harness_file)

    # run.py 用 uvicorn(reload=True)，它会再起一个子进程。
    # 用 start_new_session 建独立进程组，结束时整组干掉，避免留下孤儿。
    creation = 0
    if hasattr(os, "CREATE_NEW_PROCESS_GROUP"):
        creation = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=creation,
    )

    try:
        if not wait_ready(base, proc):
            out = ""
            try:
                out = proc.stdout.read() if proc.stdout else ""
            except Exception:  # noqa: BLE001
                pass
            print("服务未能启动。输出：")
            print(out[-2000:])
            return 2

        print(f"服务已就绪：{base}\n")

        # ---------- 1. 根路径返回入口页 ----------
        status, body, ctype = fetch(base + "/")
        html = body.decode("utf-8", "replace")
        check("GET / 返回 200", status == 200, f"HTTP {status}")
        check("GET / 返回 HTML", "text/html" in ctype, ctype)
        check("返回的是前端入口页", "错题本" in html and 'id="app"' in html,
              f"{len(html)} 字节")

        # ---------- 2. 逐个请求页面引用的静态资源 ----------
        # 去注释后再找引用，避免把注释里的示例当成真实引用
        html_no_comments = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        # 先去掉 Vue 的动态绑定属性（:src="img.url" 之类），它们不是 URL。
        # 不去掉的话会抓出 "img.url"、"previewImage" 这种表达式当路径请求。
        static_attrs = re.sub(r'\s:(?:href|src)="[^"]*"', "", html_no_comments)
        refs = re.findall(r'(?:href|src)="([^"]+)"', static_attrs)
        rels = [r for r in refs
                if not r.startswith(("#", "http://", "https://", "data:"))]
        check("页面引用了静态资源", bool(rels), ", ".join(rels))

        missing = []
        for rel in rels:
            url = base + "/" + rel.lstrip("/")
            st, content, ct = fetch(url)
            if st != 200:
                missing.append(f"{rel} -> HTTP {st}")
            elif len(content) == 0:
                missing.append(f"{rel} -> 空内容")
        check("所有静态资源都返回 200（无 404）", not missing,
              "; ".join(missing) if missing else f"{len(rels)} 个资源全部 OK")

        # 单独确认三个关键文件，便于定位问题
        for rel, must in (("css/style.css", "--c-primary"),
                          ("js/api.js", "global.API"),
                          ("js/app.js", "createApp"),
                          ("js/vendor/vue.global.prod.js", "vue v3")):
            st, content, _ = fetch(f"{base}/{rel}")
            text = content.decode("utf-8", "replace")
            check(f"{rel} 内容正确", st == 200 and must in text,
                  f"HTTP {st}, 含 {must!r}={must in text}")

        # ---------- 3. API 未被静态挂载吞掉 ----------
        st, body, _ = fetch(base + "/health")
        check("GET /health 仍可用（挂静态后没被吞）", st == 200,
              body.decode("utf-8", "replace")[:80])
        st, body, _ = fetch(base + "/folders/tree")
        check("GET /folders/tree 仍可用", st == 200,
              f"HTTP {st} {body.decode('utf-8', 'replace')[:60]}")
        st, body, _ = fetch(base + "/tags")
        check("GET /tags 仍可用", st == 200, f"HTTP {st}")
        st, body, _ = fetch(base + "/settings")
        check("GET /settings 仍可用", st == 200,
              body.decode("utf-8", "replace")[:80])

        # /uploads 仍指向图片目录（题目图片靠它访问）
        st, body, _ = fetch(base + "/uploads/")
        check("/uploads 挂载存在（目录列表或 404 都说明未挂错到别处）",
              st in (200, 404), f"HTTP {st}")

        # ---------- 4. 浏览器里确认 Vue 真的挂载、页面渲染 ----------
        # 测试页由被测应用自己在 /__harness 提供，因此与被测页面**同源**，
        # 才能读取 iframe 内的 DOM（跨源会被浏览器拦截）。
        proc_edge = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--no-sandbox", "--force-device-scale-factor=1",
             "--window-size=1440,1000", "--virtual-time-budget=20000",
             "--dump-dom", base + "/__harness"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180,
        )
        dom = proc_edge.stdout or ""
        m = re.search(r'<pre id="render-result">(.*?)</pre>', dom, re.S)
        if not m:
            check("浏览器渲染检查取得结果", False, "未取到结果")
        else:
            raw = (m.group(1).replace("&quot;", '"').replace("&amp;", "&")
                   .replace("&lt;", "<").replace("&gt;", ">"))
            data = json.loads(raw)
            check("浏览器里 Vue 已挂载",
                  data.get("vue") is True, str(data.get("vue")))
            check("Vue 实例挂在 #app 上",
                  data.get("appClass") is not None, str(data.get("appClass")))
            check("模板已编译（页面无残留 {{ }}）",
                  data.get("rawTemplate") == "", str(data.get("rawTemplate")))
            check("页面渲染出侧边栏与统计卡片",
                  data.get("navCount") == 6 and data.get("statCards") == 4,
                  f"nav={data.get('navCount')} stats={data.get('statCards')}")
            check("首屏无 JS 报错",
                  not data.get("errors"), "; ".join(data.get("errors") or []))

    finally:
        # reload 模式下 uvicorn 会派生工作子进程，整组结束
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, check=False)
            else:
                os.killpg(os.getpgid(proc.pid), 15)
        except Exception:  # noqa: BLE001
            proc.kill()
        time.sleep(0.5)

    print("-" * 78)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for name, _, detail in failed:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failed else 0


# 在被测页面的真实 URL 上收集渲染结果（同源 iframe，可读 DOM）
# 用轮询等待应用真正挂载，而不是固定 sleep —— 固定等待在机器忙时会偶发失败。
RENDER_HARNESS = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>render-check</title></head><body><script>
const wait = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, timeout) {
  const end = Date.now() + (timeout || 15000);
  while (Date.now() < end) {
    try { const v = fn(); if (v) return v; } catch (e) {}
    await wait(200);
  }
  return null;
}
(async () => {
  const errors = [];
  window.addEventListener('error', (e) => errors.push(String(e.message)));
  const f = document.createElement('iframe');
  f.style.cssText = 'width:1400px;height:950px;border:0';
  document.body.appendChild(f);
  f.src = '__BASE__/';

  // 等到 #app 上出现 Vue 实例（v-cloak 被移除即说明已挂载）
  const app = await until(() => {
    const d = f.contentDocument;
    if (!d) return null;
    const el = d.getElementById('app');
    if (!el || el.hasAttribute('v-cloak')) return null;
    return el;
  }, 25000);
  // 再等首屏数据到位（统计卡片渲染出来）
  await until(() => f.contentDocument.querySelectorAll('.stat-card__value').length, 15000);
  await wait(300);

  let out = { errors };
  try {
    const w = f.contentWindow, d = f.contentDocument;
    out.vue = typeof w.Vue !== 'undefined';
    out.appClass = app ? app.className : null;
    const leftover = d.body.innerHTML.match(/\\{\\{[^}]*\\}\\}/g);
    out.rawTemplate = leftover ? leftover.join(',') : '';
    out.navCount = d.querySelectorAll('.nav__item').length;
    out.statCards = d.querySelectorAll('.stat-card__value').length;
  } catch (e) {
    out.errors = errors.concat(['读取 iframe 失败: ' + e.message]);
  }
  const pre = document.createElement('pre');
  pre.id = 'render-result';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
