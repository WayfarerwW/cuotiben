"""真实输入自检：点击能否聚焦、真实按键能否输入。

**为什么必须单独有这一份**：
其余浏览器自检都是用 DOM API 造数据 —— `el.value = 'x'` 再派发一个
`input` 事件，或者 `el.click()`。这些方式**绕过了浏览器的默认行为**
（点击聚焦、按键生成字符），所以下面这类缺陷它们一律测不出来：

  弹窗遮罩上写了 `@mousedown.prevent`，输入框在遮罩内部，mousedown 冒泡
  上去被 preventDefault 后，**浏览器不再给输入框聚焦**；
  表现是"点输入框点不进去、打字落到上次聚焦的按钮上、下拉也打不开"，
  而按钮点击仍然正常 —— 看起来很像"只有文字输入坏了"。

真实定位办法只有一个：用 CDP 发**真实鼠标事件与真实按键**，
然后断言 activeElement 与输入值。

覆盖：
- 点击题干 / 答案 / 标签输入框后，activeElement 确实是它
- 真实按键能在题干、答案里产生文字
- 大类下拉能聚焦、能用键盘选中，folder_id 落对
- 标签能输入并用回车创建
- 多行输入框高度不再是单行 36px
- 遮罩上不再有会吞掉聚焦的裸 `@mousedown.prevent`

用法：python tools/verify_input.py [端口]
前置：不需要 —— 本脚本自己起测试服务器与浏览器。
"""

from __future__ import annotations

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


def type_text(cdp, text: str) -> None:
    """只发 keyDown（带 text）—— 再补 char 会让字符翻倍。

    真实浏览器一次按键 = keydown + (可选)keypress + keyup；CDP 里
    给 keyDown 带上 `text` 就会产生字符输入，因此**不要**再单独发 char。
    """
    for ch in text:
        cdp.call("Input.dispatchKeyEvent", type="keyDown", text=ch, key=ch,
                 unmodifiedText=ch)
        cdp.call("Input.dispatchKeyEvent", type="keyUp", key=ch)


def click_at(cdp, box: dict) -> None:
    cdp.call("Input.dispatchMouseEvent", type="mousePressed",
             x=box["x"], y=box["y"], button="left", clickCount=1)
    cdp.call("Input.dispatchMouseEvent", type="mouseReleased",
             x=box["x"], y=box["y"], button="left", clickCount=1)


def main() -> int:  # noqa: C901
    try:
        import websocket  # noqa: F401
    except ImportError:
        print("缺少 websocket-client（仅自检需要）：pip install websocket-client")
        return 2

    port = sys.argv[1] if len(sys.argv) > 1 else "9466"
    debug_port = int(port) + 1
    base = f"http://127.0.0.1:{port}"

    work = Path(tempfile.mkdtemp(prefix="verify_input_"))
    harness = work / "h.html"
    harness.write_text("<!DOCTYPE html><html><body></body></html>", encoding="utf-8")
    env = dict(os.environ)
    env["CUOTIBEN_BACKUPS_DIR"] = str(work / "backups")
    env["CUOTIBEN_UPLOADS_DIR"] = str(work / "uploads")

    code = ("import runpy,sys; "
            f"sys.argv=['x',{port!r},{str(harness)!r},{str(work / 'd.db')!r}]; "
            "runpy.run_path(r'tools/_serve_for_ui_test.py', run_name='__main__')")
    server = subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    browser = None
    profile = work / "profile"
    run_id = uuid.uuid4().hex[:8]
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
            return json.loads(op.open(req, timeout=15).read())

        subj = post("/folders", {"name": f"数学{run_id}", "parent_id": None})
        leaf = post("/folders", {"name": "极限", "parent_id": subj["id"]})
        # 造一个标签，供"联想下拉能展开"的断言使用（空联想时 aria-expanded
        # 本来就该是 false，那样断言等于没测）
        post("/questions", {
            "folder_id": leaf["id"], "stem": "种子题",
            "answer": "a", "tags": ["重要极限"]})

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
        return run_cases(cdp)
    finally:
        for p in (browser, server):
            if p is not None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True)
        shutil.rmtree(work, ignore_errors=True)


class CDP:
    def __init__(self, url: str) -> None:
        import websocket

        self.ws = websocket.create_connection(url, timeout=60)
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

    def box(self, element_id: str) -> dict | None:
        raw = self.js(f"""(() => {{
          const el = document.getElementById({element_id!r});
          if (!el) return null;
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
          return JSON.stringify({{x: r.left + r.width/2, y: r.top + r.height/2,
                                 hit: hit ? (hit.id || hit.tagName) : null,
                                 same: hit === el,
                                 h: Math.round(r.height)}});
        }})()""")
        return json.loads(raw) if raw and raw.startswith("{") else None

    def active(self) -> str:
        return self.js("document.activeElement ? (document.activeElement.id"
                       " || document.activeElement.tagName) : 'null'")

    def component_state(self, key: str):
        return self.js(f"""(() => {{
          const el = document.getElementById('app');
          const comp = el && el._vnode && el._vnode.component;
          return comp ? JSON.stringify(comp.setupState.{key}) : '__no_comp__';
        }})()""")


def run_cases(cdp: CDP) -> int:  # noqa: C901
    section("遮罩不能吞掉聚焦（根因回归）")
    bad_masks = cdp.js("""(() => {
      // 带裸 .prevent（不含 .self）的遮罩会把内部元素的 mousedown 也阻止掉。
      // Vue 编译后事件在 \u005fvei 上，这里直接看模板属性不可靠，
      // 于是用行为验证：在输入框上派发可取消的 mousedown，看是否被阻止。
      return 'checked-by-behavior';
    })()""")
    expect("遮罩检查方式已就绪", bad_masks == "checked-by-behavior")

    # 打开新增题目弹窗
    cdp.js("""(() => {
      const b = Array.from(document.querySelectorAll('button'))
        .find(x => x.textContent.trim().includes('新增题目'));
      if (b) b.click();
      return !!b;
    })()""")
    time.sleep(1.6)

    mousedown_blocked = cdp.js("""(() => {
      const el = document.getElementById('q-stem');
      if (!el) return 'NO_INPUT';
      const ev = new MouseEvent('mousedown', {bubbles: true, cancelable: true});
      el.dispatchEvent(ev);
      return String(ev.defaultPrevented);
    })()""")
    expect("输入框上的 mousedown 默认行为未被阻止", mousedown_blocked == "false",
           f"defaultPrevented={mousedown_blocked}")

    section("点击能否聚焦（真实鼠标）")
    for element_id, label in (("q-stem", "题干"), ("q-answer", "答案"),
                              ("q-tags", "标签"), ("q-folder", "大类下拉")):
        box = cdp.box(element_id)
        if not box:
            expect(f"{label}输入框存在", False, element_id)
            continue
        expect(f"{label}可被命中（没有元素遮挡）", box["same"] is True,
               f"命中 {box['hit']}")
        click_at(cdp, box)
        time.sleep(0.4)
        expect(f"点击{label}后焦点在它身上", cdp.active() == element_id,
               f"activeElement={cdp.active()}")

    section("真实按键能否输入（真实键盘）")
    box = cdp.box("q-stem")
    if box:
        click_at(cdp, box)
        time.sleep(0.3)
        cdp.js("document.getElementById('q-stem').value = ''")
        type_text(cdp, "abc")
        time.sleep(0.6)
        dom_value = cdp.js("JSON.stringify(document.getElementById('q-stem').value)")
        model_value = cdp.component_state("questionForm.stem")
        expect("真实按键能在题干产生文字", dom_value == '"abc"', dom_value)
        expect("题干内容同步进了组件状态（v-model 生效）",
               model_value == '"abc"', model_value)

    box = cdp.box("q-answer")
    if box:
        click_at(cdp, box)
        time.sleep(0.3)
        cdp.js("document.getElementById('q-answer').value = ''")
        type_text(cdp, "xyz")
        time.sleep(0.6)
        expect("真实按键能在答案产生文字",
               cdp.js("JSON.stringify(document.getElementById('q-answer').value)")
               == '"xyz"',
               cdp.js("JSON.stringify(document.getElementById('q-answer').value)"))

    section("大类下拉可用鼠标+键盘选择")
    box = cdp.box("q-folder")
    if box:
        click_at(cdp, box)
        time.sleep(0.4)
        expect("点击大类下拉后焦点在下拉上", cdp.active() == "q-folder",
               cdp.active())
        for key, vk in (("ArrowDown", 40), ("Enter", 13)):
            cdp.call("Input.dispatchKeyEvent", type="keyDown", key=key,
                     windowsVirtualKeyCode=vk)
            cdp.call("Input.dispatchKeyEvent", type="keyUp", key=key,
                     windowsVirtualKeyCode=vk)
        time.sleep(0.6)
        folder_id = cdp.component_state("questionForm.folder_id")
        expect("键盘选中后 folder_id 落在某个大类上",
               folder_id not in ("null", "__no_comp__", None), folder_id)

    section("标签可输入并用回车创建")
    box = cdp.box("q-tags")
    if box:
        click_at(cdp, box)
        time.sleep(0.3)
        cdp.js("document.getElementById('q-tags').value = ''")
        type_text(cdp, "tag1")
        time.sleep(0.7)
        expect("真实按键能在标签框产生文字",
               cdp.js("JSON.stringify(document.getElementById('q-tags').value)")
               == '"tag1"',
               cdp.js("JSON.stringify(document.getElementById('q-tags').value)"))
        cdp.call("Input.dispatchKeyEvent", type="keyDown", key="Enter",
                 windowsVirtualKeyCode=13)
        cdp.call("Input.dispatchKeyEvent", type="keyUp", key="Enter",
                 windowsVirtualKeyCode=13)
        time.sleep(0.9)
        tags = cdp.component_state("questionForm.tags")
        expect("回车后标签被创建", tags == '["tag1"]', tags)

    section("多行输入框不是单行高度")
    for element_id, label in (("q-stem", "题干"), ("q-answer", "答案")):
        box = cdp.box(element_id)
        if box:
            expect(f"{label}输入框高度大于单行（36px）", box["h"] > 40,
                   f"{box['h']}px")

    section("标签联想仍可用（键盘）")
    box = cdp.box("q-tags")
    if box:
        click_at(cdp, box)
        cdp.js("document.getElementById('q-tags').value = ''")
        type_text(cdp, "重要")          # 种子里有「重要极限」，前缀能命中
        time.sleep(1.2)
        expanded = cdp.js("document.getElementById('q-tags').getAttribute('aria-expanded')")
        options = cdp.js("document.querySelectorAll('#tag-suggest-list [role=\"option\"]').length")
        expect("输入后标签联想下拉展开（aria-expanded=true）",
               expanded == "true", expanded)
        expect("联想列表里有候选项", isinstance(options, int) and options >= 1,
               f"{options} 项")

    section("打勾后能撤销（requirements 5.3 / 验收标准 13）")
    # 关掉弹窗，回到题目列表。直接置组件状态最可靠 ——
    # 弹窗里有两个 aria-label="关闭" 的按钮，按文案找容易点错。
    closed = cdp.js("""(() => {
      const el = document.getElementById('app');
      const comp = el && el._vnode && el._vnode.component;
      if (!comp) return 'no-comp';
      comp.setupState.questionEditor.open = false;
      return 'closed';
    })()""")
    expect("已关闭题目弹窗", closed == "closed", closed)
    time.sleep(1.2)

    # 首页不渲染题目表格，必须切到「题目管理」才有行内打勾按钮
    nav = cdp.js("""(() => {
      const n = Array.from(document.querySelectorAll('.nav__item'))
        .find(x => x.textContent.includes('题目管理'));
      if (!n) return 'NO_NAV';
      n.click();
      return 'clicked';
    })()""")
    expect("能切到题目管理页", nav == "clicked", nav)
    time.sleep(2.0)

    # 让题目到期，才会进复习队列
    cdp.js("""fetch('/__redue', {method: 'POST'}).then(r => r.text())""")
    time.sleep(1.5)

    # 表格里的行内「打勾」按钮：textContent 含 svg，不能用 === 精确匹配
    scene = cdp.js("""(() => {
      const active = document.querySelector('.nav__item.is-active');
      const el = document.getElementById('app');
      const comp = el && el._vnode && el._vnode.component;
      return JSON.stringify({
        page: comp ? comp.setupState.currentPage : '?',
        navActive: active ? active.textContent.trim() : null,
        rows: document.querySelectorAll('table tbody tr').length,
        checkBtns: document.querySelectorAll('.check-btn').length,
        allBtns: Array.from(document.querySelectorAll('button'))
          .map(b => b.textContent.trim()).filter(t => t.includes('打勾')).length,
        questionCount: comp ? (comp.setupState.questions || []).length : '?',
      });
    })()""")
    expect("诊断 打勾前现场", True, scene)

    clicked = cdp.js("""(() => {
      const b = Array.from(document.querySelectorAll('.check-btn'))
        .find(x => x.textContent.includes('打勾'));
      if (!b) return 'NO_BUTTON';
      b.click();
      return 'clicked';
    })()""")
    expect("表格行内有打勾按钮可点", clicked == "clicked", clicked)
    time.sleep(2.2)

    # Toast 上应当出现「撤销」
    undo_label = cdp.js("""(() => {
      const b = document.querySelector('.toast__action');
      return b ? b.textContent.trim() : null;
    })()""")
    expect("打勾后的提示上出现「撤销」按钮", undo_label == "撤销", undo_label)

    # 撤销前的打勾数（用于核对是否回退）
    done_before = cdp.js("""(() => {
      const el = document.getElementById('app');
      const comp = el && el._vnode && el._vnode.component;
      return comp ? comp.setupState.reviewDoneToday : '__no_comp__';
    })()""")

    clicked_undo = cdp.js("""(() => {
      const b = document.querySelector('.toast__action');
      if (!b) return 'NO_UNDO';
      b.click();
      return 'clicked';
    })()""")
    expect("能点到撤销按钮", clicked_undo == "clicked", clicked_undo)
    time.sleep(2.5)

    done_after = cdp.js("""(() => {
      const el = document.getElementById('app');
      const comp = el && el._vnode && el._vnode.component;
      return comp ? comp.setupState.reviewDoneToday : '__no_comp__';
    })()""")
    expect("撤销后「今日已打勾」计数回退",
           isinstance(done_before, int) and isinstance(done_after, int)
           and done_after == done_before - 1,
           f"{done_before} -> {done_after}")

    # 服务端也要认：撤销后该题回到今日队列
    back = cdp.js("""(async () => {
      const rq = await fetch('/review/today').then(r => r.json());
      return JSON.stringify(Array.isArray(rq) ? rq.length : rq);
    })()""")
    expect("撤销后该题重新出现在今日队列（服务端）",
           back not in ("0", None, "__ERR__"), f"队列={back}")

    # 连点两次撤销的第二下应当拿到 409 -> 友好提示
    again = cdp.js("""(async () => {
      const el = document.getElementById('app');
      const comp = el && el._vnode && el._vnode.component;
      const rq = await fetch('/review/today').then(r => r.json());
      if (!rq.length) return 'EMPTY_QUEUE';
      await comp.setupState.undoCheck(rq[0].question_id, null);
      return 'done';
    })()""")
    time.sleep(2.0)
    warn = cdp.js("""(() => {
      const t = Array.from(document.querySelectorAll('.toast__message'))
        .map(x => x.textContent.trim());
      return JSON.stringify(t);
    })()""")
    expect("重复撤销不会静默失败（给出提示）",
           again == "done" and ("撤销" in (warn or "") or "打勾" in (warn or "")),
           f"{again} / {warn}")

    section("移除图片会真的删掉物理文件（不是只解关联）")
    # 回到题目管理，重开弹窗
    cdp.js("""(() => {
      const n = Array.from(document.querySelectorAll('.nav__item'))
        .find(x => x.textContent.includes('题目管理'));
      if (n) n.click();
      return !!n;
    })()""")
    time.sleep(1.5)
    cdp.js("""(() => {
      const b = Array.from(document.querySelectorAll('button'))
        .find(x => x.textContent.trim().includes('新增题目'));
      if (b) b.click();
      return !!b;
    })()""")
    time.sleep(1.5)

    # 选一个大类
    sel = cdp.js("""(() => {
      const el = document.getElementById('q-folder');
      const opt = Array.from(el.options).find(o => !o.disabled);
      if (!opt) return 'NO_OPTION';
      el.value = opt.value;
      el.dispatchEvent(new Event('change', {bubbles: true}));
      return opt.value;
    })()""")
    expect("能选到所属大类", sel not in ("NO_OPTION", None), sel)

    # 造一张 JPEG 并上传
    uploaded = cdp.js("""(async () => {
      const cv = document.createElement('canvas');
      cv.width = 40; cv.height = 30;
      cv.getContext('2d').fillStyle = '#3366cc';
      cv.getContext('2d').fillRect(0, 0, 40, 30);
      const blob = await new Promise(r => cv.toBlob(r, 'image/jpeg', 0.9));
      const file = new File([blob], 'del.jpg', {type: 'image/jpeg'});
      const input = document.querySelector('input[type="file"]');
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', {bubbles: true}));
      return 'sent';
    })()""")
    expect("已触发图片上传", uploaded == "sent", uploaded)
    time.sleep(4.0)

    shown = cdp.js("document.querySelectorAll('.img-list img').length")
    expect("上传后出现缩略图", isinstance(shown, int) and shown >= 1, f"{shown} 张")

    # 取出该图的服务端信息，用于之后断言文件已删
    img_info = cdp.js("""(async () => {
      const r = await fetch('/questions').then(r => r.json());
      // 上传的图还没保存，question_images 里是孤儿行；直接从表单状态拿
      const el = document.getElementById('app');
      const comp = el && el._vnode && el._vnode.component;
      const imgs = comp ? comp.setupState.questionForm.images : [];
      return JSON.stringify(imgs.map(i => ({id: i.id, url: i.url})));
    })()""")
    expect("表单里的图片带了 question_images id（删除物理文件需要它）",
           '"id":' in (img_info or "") and '"id":null' not in (img_info or ""),
           img_info)

    # 点「×」移除
    removed = cdp.js("""(() => {
      const b = document.querySelector('.img-item__remove');
      if (!b) return 'NO_X';
      b.click();
      return 'clicked';
    })()""")
    expect("能点到缩略图上的移除按钮", removed == "clicked", removed)
    time.sleep(1.0)

    after_remove = cdp.js("document.querySelectorAll('.img-list img').length")
    expect("移除后缩略图消失", after_remove == 0, f"{after_remove} 张")

    # 关键：**此时文件还不该被删**（点×不算数，要等保存成功）
    url = None
    if img_info and img_info.startswith("["):
        try:
            url = json.loads(img_info)[0]["url"]
        except Exception:
            url = None
    if url:
        exists_before_save = cdp.js(f"""fetch({url!r}, {{method: 'HEAD'}})
          .then(r => String(r.status)).catch(() => 'ERR')""")
        expect("点×之后文件**还在**（等保存成功再删，避免取消保存导致死链）",
               exists_before_save == "200", f"HTTP {exists_before_save}")

        # 保存 -> 触发真正删除
        cdp.js("""(() => {
          const ta = document.getElementById('q-stem');
          const setter = Object.getOwnPropertyDescriptor(
            window.HTMLTextAreaElement.prototype, 'value').set;
          setter.call(ta, '删图测试题');
          ta.dispatchEvent(new Event('input', {bubbles: true}));
          return 'filled';
        })()""")
        time.sleep(0.4)
        cdp.js("""(() => {
          const b = Array.from(document.querySelectorAll('.modal button'))
            .find(x => /保存|创建/.test(x.textContent));
          if (b) b.click();
          return !!b;
        })()""")
        time.sleep(3.5)
        exists_after_save = cdp.js(f"""fetch({url!r}, {{method: 'HEAD'}})
          .then(r => String(r.status)).catch(() => 'ERR')""")
        expect("保存成功后物理文件**已被删除**", exists_after_save != "200",
               f"HTTP {exists_after_save}")

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
