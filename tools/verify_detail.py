"""题目详情浮层自检。

覆盖（requirements 2.3 的"浏览"场景）：
- 列表题干列可点，点开只读详情
- **答案默认遮住，且是"真的不在 DOM 里"** —— 只用 CSS 藏起来不算，
  那样 Ctrl+A / 查看源码就能看到，"遮住答案"形同虚设
- 点「显示答案」后答案进 DOM，按钮变「收起答案」；再点能收起
- 只读：详情里没有「打勾」，浏览不会改动复习计划
- 打开后焦点移入、Tab 不逃逸、关闭后焦点归还到那一行
- 深链接 `#/questions/q/{id}` 能直接打开（刷新/书签场景）
- 纯图片题目（无文字题干）也能打开并看到图
- 列表题干列有缩略图（纯图片题目否则认不出是哪道题）

用法：python tools/verify_detail.py [端口]
自检自己起测试服务器与浏览器，不需要预先启动服务。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

OK = FAIL = 0
FAILED: list[str] = []


def expect(label: str, cond: bool, detail: object = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        FAILED.append(label)
    print(("[PASS] " if cond else "[FAIL] ") + label
          + (f"  ({detail})" if detail != "" else ""))


def section(title: str) -> None:
    print(f"\n-- {title} --")


def _jpeg(w: int = 60, h: int = 40, color: tuple = (51, 102, 204)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=88)
    return buf.getvalue()


class CDP:
    def __init__(self, url: str) -> None:
        import websocket

        self.ws = websocket.create_connection(url, timeout=90)
        self.n = 0

    def call(self, method: str, **params):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr,
                      returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            return f"__ERR__ {r['exceptionDetails'].get('text')}"
        return r.get("result", {}).get("value")

    def box(self, selector: str):
        """元素中心点 + 命中测试（确认没有被遮挡）。"""
        raw = self.js(f"""(() => {{
          const el = document.querySelector({selector!r});
          if (!el) return null;
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2,
                                                r.top + r.height / 2);
          return JSON.stringify({{x: r.left + r.width / 2, y: r.top + r.height / 2,
                                  same: hit === el || el.contains(hit)}});
        }})()""")
        return json.loads(raw) if raw and raw.startswith("{") else None

    def click_at(self, box: dict) -> None:
        self.call("Input.dispatchMouseEvent", type="mousePressed",
                  x=box["x"], y=box["y"], button="left", clickCount=1)
        self.call("Input.dispatchMouseEvent", type="mouseReleased",
                  x=box["x"], y=box["y"], button="left", clickCount=1)

    def key(self, key: str, vk: int) -> None:
        # 只发 keyDown/keyUp：keyDown 不带 text 不会产生字符输入
        self.call("Input.dispatchKeyEvent", type="keyDown", key=key,
                  windowsVirtualKeyCode=vk)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=key,
                  windowsVirtualKeyCode=vk)


def main() -> int:  # noqa: C901
    try:
        import websocket  # noqa: F401
    except ImportError:
        print("缺少 websocket-client（仅自检需要）：pip install websocket-client")
        return 2

    port = sys.argv[1] if len(sys.argv) > 1 else "9700"
    debug_port = int(port) + 1
    base = f"http://127.0.0.1:{port}"

    # 答案里放一个独一无二的串：用于在整页 HTML 里搜"答案有没有泄漏"。
    # 遮住必须是**真的不渲染**，所以断言方式是"HTML 里搜不到它"。
    secret = "ANSWERLEAK" + uuid.uuid4().hex[:8].upper()

    work = Path(tempfile.mkdtemp(prefix="verify_detail_"))
    harness = work / "h.html"
    harness.write_text("<!DOCTYPE html><html><body></body></html>", encoding="utf-8")
    env = dict(os.environ)
    env["CUOTIBEN_BACKUPS_DIR"] = str(work / "backups")
    env["CUOTIBEN_UPLOADS_DIR"] = str(work / "uploads")
    env["GIT_SYNC_ENABLED"] = "0"

    code = ("import runpy,sys; "
            f"sys.argv=['x',{port!r},{str(harness)!r},{str(work / 'd.db')!r}]; "
            "runpy.run_path(r'tools/_serve_for_ui_test.py', run_name='__main__')")
    server = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    browser = None
    profile = work / "profile"
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ready = False
        for _ in range(60):
            time.sleep(0.5)
            try:
                op.open(base + "/health", timeout=2).read()
                ready = True
                break
            except Exception:
                pass
        if not ready:
            print("测试服务器未就绪")
            return 2

        def post(path, payload):
            req = urllib.request.Request(
                base + path, data=json.dumps(payload).encode("utf-8"), method="POST")
            req.add_header("Content-Type", "application/json")
            return json.loads(op.open(req, timeout=20).read())

        def upload(jpg: bytes, name: str) -> dict:
            b = "----d" + uuid.uuid4().hex
            body = (f"--{b}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
                    "Content-Type: image/jpeg\r\n\r\n").encode() + jpg + \
                   f"\r\n--{b}--\r\n".encode()
            req = urllib.request.Request(base + "/upload/image", data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={b}")
            return json.loads(op.open(req, timeout=30).read())

        subj = post("/folders", {"name": "数学"})
        leaf = post("/folders", {"name": "极限", "parent_id": subj["id"]})

        stem_img = upload(_jpeg(), "s.jpg")
        ans_img = upload(_jpeg(50, 50, (200, 60, 60)), "a.jpg")

        # 题 1：文字 + 题干图 + 答案图（答案里藏 secret）
        q1 = post("/questions", {
            "folder_id": leaf["id"], "stem": "求极限 lim(x→0) sin(x)/x",
            "answer": "答案是 1。" + secret, "tags": ["重要极限"],
            "is_starred": True, "sort_order": 0,
            "images": [{"url": stem_img["file_path"], "kind": "stem"},
                       {"url": ans_img["file_path"], "kind": "answer"}],
        })
        # 题 2：纯图片，无任何文字
        q2 = post("/questions", {
            "folder_id": leaf["id"], "stem": None, "answer": None,
            "tags": [], "sort_order": 1,
            "images": [{"url": upload(_jpeg(80, 60, (40, 160, 90)), "o.jpg")["file_path"],
                        "kind": "stem"}],
        })
        print(f"种子：题 {q1['id']}（文字+两面图）、题 {q2['id']}（纯图片）")

        browser = subprocess.Popen(
            [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--remote-debugging-port={debug_port}",
             "--remote-allow-origins=*",
             f"--user-data-dir={profile}", "--window-size=1500,1100",
             base + "/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        ws_url = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                data = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{debug_port}/json/list", timeout=3).read())
                pages = [t for t in data if t.get("type") == "page"
                         and f"127.0.0.1:{port}" in t.get("url", "")]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
        if not ws_url:
            print("连不上浏览器调试端口")
            return 2

        cdp = CDP(ws_url)
        cdp.call("Runtime.enable")
        time.sleep(3)
        return run_cases(cdp, q1, q2, secret)
    finally:
        for p in (browser, server):
            if p is not None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


def run_cases(cdp: CDP, q1: dict, q2: dict, secret: str) -> int:  # noqa: C901
    section("列表入口")
    cdp.js("""(() => {
      const n = Array.from(document.querySelectorAll('.nav__item'))
        .find(x => x.textContent.includes('题目管理'));
      if (n) n.click();
      return !!n;
    })()""")
    time.sleep(2.0)

    rows = cdp.js("document.querySelectorAll('table tbody tr').length")
    expect("题目管理页列出了题目", isinstance(rows, int) and rows >= 2, f"{rows} 行")

    btns = cdp.js("document.querySelectorAll('.stem-open').length")
    expect("每题题干列都有可点按钮（键盘可聚焦）",
           isinstance(btns, int) and btns >= 2, f"{btns} 个")

    thumbs = cdp.js("document.querySelectorAll('.stem-thumb').length")
    expect("题干列有缩略图（纯图片题目否则认不出是哪道题）",
           isinstance(thumbs, int) and thumbs >= 1, f"{thumbs} 个")

    section("答案默认遮住")
    # **必须用真实鼠标点击**：`el.click()` 是合成事件、**不会先聚焦元素**，
    # 而真实点击顺序是 mousedown -> focus -> click。用合成事件的话
    # rememberFocus() 拿到的会是 body，于是"关闭后焦点归还"永远测不过 ——
    # 那是自检不忠实，不是产品缺陷。第一次就是这么误判的。
    target = cdp.js("""(() => {
      const bs = Array.from(document.querySelectorAll('.stem-open'));
      const t = bs.find(b => b.textContent.includes('lim'));
      if (!t) return null;
      t.id = 'detail-probe';
      return 'marked';
    })()""")
    expect("找到了有文字题干的题目", target == "marked", target)
    box = cdp.box("#detail-probe")
    expect("题干按钮可被命中（没有元素遮挡）",
           bool(box) and box["same"] is True, box)
    if box:
        cdp.click_at(box)
    time.sleep(1.6)

    expect("详情浮层已打开", cdp.js(
        "!!document.querySelector('.modal--detail')") is True)

    stem_shown = cdp.js("""(() => {
      const el = document.querySelector('.modal--detail .review__stem');
      return el ? el.textContent.trim() : null;
    })()""")
    expect("能看到题干", "lim" in (stem_shown or ""), stem_shown)

    # **关键**：遮住必须是"真的不在 DOM 里"，不能只是 CSS 藏起来
    leak = cdp.js(f"document.documentElement.innerHTML.includes({secret!r})")
    expect("**答案文本不在 DOM 里**（不是只用 CSS 藏起来）",
           leak is False, f"leak={leak}")
    veil = cdp.js("""(() => {
      const el = document.querySelector('.detail__veil');
      return el ? el.textContent.trim().slice(0, 30) : null;
    })()""")
    expect("答案区显示遮罩提示", "遮住" in (veil or ""), veil)
    expect("展开状态 aria-expanded=false",
           cdp.js("""(() => {
             const b = document.querySelector('.modal--detail .answer-toggle');
             return b ? b.getAttribute('aria-expanded') : null;
           })()""") == "false")

    section("揭开 / 收起答案")
    cdp.js("""(() => {
      document.querySelector('.modal--detail .answer-toggle').click();
      return true;
    })()""")
    time.sleep(0.9)
    leak2 = cdp.js(f"document.documentElement.innerHTML.includes({secret!r})")
    expect("**揭开后答案文本进入 DOM**", leak2 is True, f"leak={leak2}")
    expect("遮罩提示已消失",
           cdp.js("!!document.querySelector('.detail__veil')") is False)
    label = cdp.js("""(() => {
      const b = document.querySelector('.modal--detail .answer-toggle');
      return b ? b.textContent.trim() : null;
    })()""")
    expect("按钮文案变成「收起答案」", "收起" in (label or ""), label)
    expect("展开状态 aria-expanded=true",
           cdp.js("""(() => {
             return document.querySelector('.modal--detail .answer-toggle')
               .getAttribute('aria-expanded');
           })()""") == "true")
    ans_imgs = cdp.js("""(() => {
      return document.querySelectorAll('.modal--detail #detail-answer img').length;
    })()""")
    expect("答案图在揭开后才出现",
           isinstance(ans_imgs, int) and ans_imgs >= 1, f"{ans_imgs} 张")

    cdp.js("""(() => {
      document.querySelector('.modal--detail .answer-toggle').click();
      return true;
    })()""")
    time.sleep(0.9)
    leak3 = cdp.js(f"document.documentElement.innerHTML.includes({secret!r})")
    expect("**再收起后答案又离开 DOM**", leak3 is False, f"leak={leak3}")

    section("只读：不改复习计划")
    check_btns = cdp.js("""(() => {
      const d = document.querySelector('.modal--detail');
      return Array.from(d.querySelectorAll('button'))
        .filter(b => b.textContent.trim().includes('打勾')).length;
    })()""")
    expect("**详情里没有「打勾」按钮**（打勾会改复习计划）",
           check_btns == 0, f"{check_btns} 个")
    foot = cdp.js("""(() => {
      const d = document.querySelector('.modal--detail .modal__foot');
      return d ? d.textContent.trim() : '';
    })()""")
    expect("页脚说明了只读", "只读" in (foot or ""), (foot or "")[:30])

    section("焦点与键盘")
    active = cdp.js("""(() => {
      const a = document.activeElement;
      const d = document.querySelector('.modal--detail');
      return d && d.contains(a) ? 'inside' : 'OUTSIDE';
    })()""")
    expect("打开后焦点在弹窗内", active == "inside", active)

    for _ in range(12):
        cdp.key("Tab", 9)
    time.sleep(0.5)
    inside = cdp.js("""(() => {
      const d = document.querySelector('.modal--detail');
      return d && d.contains(document.activeElement) ? 'inside' : 'escaped';
    })()""")
    expect("Tab 连按不逃出弹窗", inside == "inside", inside)

    section("深链接")
    expect("地址栏带上了题目 id 深链接",
           (cdp.js("location.hash") or "").startswith("#/questions/q/"),
           cdp.js("location.hash"))

    section("关闭与焦点归还")
    cdp.key("Escape", 27)
    time.sleep(1.2)
    expect("Esc 关闭详情",
           cdp.js("!!document.querySelector('.modal--detail')") is False)
    expect("关闭后回到普通页面 hash",
           cdp.js("location.hash") == "#/questions", cdp.js("location.hash"))
    focus_back = cdp.js("""(() => {
      const a = document.activeElement;
      if (a && a.classList && a.classList.contains('stem-open')) return 'returned';
      return a ? (a.id || a.tagName) : 'null';
    })()""")
    expect("关闭后焦点归还到题干按钮", focus_back == "returned", focus_back)

    section("纯图片题目")
    # 同样用真实鼠标点击
    cdp.js("""(() => {
      const bs = Array.from(document.querySelectorAll('.stem-open'));
      const t = bs.find(b => b.textContent.includes('仅图片'));
      if (t) t.id = 'detail-probe2';
      return !!t;
    })()""")
    box2 = cdp.box("#detail-probe2")
    if box2:
        cdp.click_at(box2)
    time.sleep(1.6)
    expect("纯图片题目也能打开详情", cdp.js(
        "!!document.querySelector('.modal--detail')") is True)
    imgs = cdp.js("""(() => {
      return document.querySelectorAll('.modal--detail .review__images img').length;
    })()""")
    expect("纯图片题目在详情里看得到图",
           isinstance(imgs, int) and imgs >= 1, f"{imgs} 张")
    expect("无题干时不显示空文本占位",
           cdp.js("""(() => {
             return !!document.querySelector('.modal--detail .review__stem');
           })()""") is False)

    section("深链接直接打开（刷新/书签场景）")
    cdp.key("Escape", 27)
    time.sleep(1.0)
    cdp.js(f"location.hash = '#/questions/q/{q1['id']}';")
    time.sleep(2.2)
    expect("改 hash 能打开对应题目",
           cdp.js("!!document.querySelector('.modal--detail')") is True)
    shown = cdp.js("""(() => {
      const el = document.querySelector('.modal--detail .review__stem');
      return el ? el.textContent.trim().slice(0, 20) : null;
    })()""")
    expect("打开的是 hash 指定的那一题", "lim" in (shown or ""), shown)
    expect("深链接打开时答案仍是遮住的",
           cdp.js(f"document.documentElement.innerHTML.includes({secret!r})") is False)

    section("点遮罩关闭")
    cdp.js("""(() => {
      const mask = document.querySelector('.modal-mask');
      const r = mask.getBoundingClientRect();
      mask.dispatchEvent(new MouseEvent('click', {
        bubbles: true, cancelable: true,
        clientX: r.left + 4, clientY: r.top + 4}));
      return true;
    })()""")
    time.sleep(1.2)
    expect("点遮罩关闭详情",
           cdp.js("!!document.querySelector('.modal--detail')") is False)

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
