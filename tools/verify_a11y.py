"""可访问性浏览器断言（ui-design 第 8 节 / AGENTS.md 第十节）。

为什么单独一个文件而不并进 verify_responsive.py：
那个文件按"三个断点"组织，这里按"键盘与焦点"组织，混在一起两边都难读。

覆盖（全部在真实浏览器里驱动，不是读源码）：
  - 弹窗打开时 body 锁背景滚动
  - 弹窗内 Tab / Shift+Tab 循环，焦点不逃出弹窗
  - 弹窗关闭后焦点归还触发元素（遮罩点击 / Esc 两条路径）
  - 下拉：↑/↓ 移动焦点并循环、Home/End、Enter 触发、Esc 关闭、点击外部关闭
  - 表格 <caption> 存在且对读屏可见（visual-hidden 而非 display:none）

用法：
    python tools/verify_responsive_a11y.py <port>
需要先用 tools/_serve_for_ui_test.py 起测试服务器。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = sys.argv[1] if len(sys.argv) > 1 else "8985"
BASE = f"http://127.0.0.1:{PORT}"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

PROBE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"></head><body>
<script>
const wait = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, ms) {
  const end = Date.now() + (ms || 15000);
  while (Date.now() < end) { try { const v = fn(); if (v) return v; } catch(e){} await wait(150); }
  return null;
}
const results = [];
function c(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
}
/* 合成一个键盘事件。原生 button 的 Enter/Space 会派发 click，
   但合成事件不会，所以需要时由测试自己再派发一次 click。 */
function key(el, k, opts) {
  const ev = new KeyboardEvent('keydown', Object.assign(
    { key: k, bubbles: true, cancelable: true }, opts || {}));
  el.dispatchEvent(ev);
  return ev;
}
function blob(el) {
  if (!el) return 'null';
  return (el.tagName || '?') + '.' + (typeof el.className === 'string' ? el.className : '');
}
(async () => {
  const f = document.createElement('iframe');
  f.style.cssText = 'border:0;height:900px;width:1400px';
  document.body.appendChild(f);
  f.src = '__BASE__/?t=' + Date.now();
  const d = await until(() => {
    const doc = f.contentDocument;
    if (!doc) return null;
    const el = doc.getElementById('app');
    return el && !el.hasAttribute('v-cloak') ? doc : null;
  }, 20000);
  if (!d) {
    c('应用挂载', false, '未挂载');
    document.body.insertAdjacentHTML('beforeend',
      '<pre id="a11y-result">' + JSON.stringify(results) + '</pre>');
    return;
  }
  const w = f.contentWindow;
  await wait(1800);

  /* 让复习队列真的有内容。POST /questions 建的首条记录 next_review_at 是
     now()+3 天，**不在**今日队列里 —— 必须显式回拨到期时间，
     否则通知面板一个菜单项都没有，↑/↓ 那几项就测了个空。 */
  try { await w.fetch('/__redue', { method: 'POST' }); } catch (e) {}
  await wait(600);

  /* ============ 1. 弹窗：锁滚动 ============ */
  /* 用真实的触发按钮路径：程序化 .click() 不会移焦点，
     而"关闭后归还焦点"依赖打开时记住的那个元素。 */
  function openModal() {
    const btn = d.querySelector('.topbar__actions .btn--primary');
    btn.focus();
    btn.click();
    return btn;
  }
  const bodyBefore = d.body.style.overflow || '(空)';
  let trigger = openModal();
  await wait(900);
  const modal = d.querySelector('.modal');
  c('弹窗打开', !!modal);
  c('弹窗打开后 body 锁背景滚动', d.body.style.overflow === 'hidden',
    'before=' + bodyBefore + ' after=' + (d.body.style.overflow || '(空)'));

  /* ============ 2. 焦点移入 + Tab 循环 ============ */
  c('打开后焦点在弹窗内',
    !!d.activeElement && modal.contains(d.activeElement), blob(d.activeElement));

  const focusables = Array.from(modal.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]),'
    + ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )).filter(el => el.offsetParent !== null);
  c('弹窗内有多个可聚焦元素', focusables.length >= 2, focusables.length + ' 个');

  // Tab 从最后一个元素 -> 应回到第一个（不逃出弹窗）
  focusables[focusables.length - 1].focus();
  key(d.activeElement, 'Tab');
  await wait(150);
  c('Tab 在最后一个元素时不逃出弹窗（回到第一个）',
    d.activeElement === focusables[0], blob(d.activeElement));

  // Shift+Tab 从第一个 -> 应回到最后一个
  focusables[0].focus();
  key(d.activeElement, 'Tab', { shiftKey: true });
  await wait(150);
  c('Shift+Tab 在第一个元素时不逃出弹窗（回到最后一个）',
    d.activeElement === focusables[focusables.length - 1], blob(d.activeElement));

  // 焦点确实没有落到弹窗外的元素上
  const outside = d.querySelector('.sidebar .nav__item');
  c('弹窗内 Tab 后焦点不在弹窗外元素上', d.activeElement !== outside, blob(d.activeElement));

  /* ============ 3. 关闭后归还焦点（遮罩点击） ============ */
  d.querySelector('.modal-mask').click();      // @click.self
  await wait(600);
  c('点击遮罩关闭弹窗', !d.querySelector('.modal'));
  c('关闭后焦点归还触发按钮', d.activeElement === trigger, blob(d.activeElement));
  c('关闭后解除背景滚动锁', d.body.style.overflow !== 'hidden',
    d.body.style.overflow || '(空)');

  /* ============ 4. Esc 关闭 + 归还焦点 ============ */
  trigger = openModal();
  await wait(800);
  c('再次打开弹窗', !!d.querySelector('.modal'));
  w.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await wait(600);
  c('Esc 关闭弹窗', !d.querySelector('.modal'));
  c('Esc 关闭后焦点归还触发按钮', d.activeElement === trigger, blob(d.activeElement));
  c('Esc 关闭后解除滚动锁', d.body.style.overflow !== 'hidden',
    d.body.style.overflow || '(空)');

  /* ============ 5. 下拉：↑/↓ 导航 ============ */
  const bell = d.getElementById('btn-bell');
  c('通知铃铛存在', !!bell);
  bell.click();
  await wait(1200);
  const panel = d.getElementById('notif-panel');
  c('通知面板打开', !!panel);
  c('面板 role=menu', panel && panel.getAttribute('role') === 'menu',
    panel ? panel.getAttribute('role') : '-');
  c('打开后焦点进入第一个菜单项',
    !!d.activeElement && d.activeElement.getAttribute('role') === 'menuitem',
    blob(d.activeElement));

  const items = Array.from(panel.querySelectorAll('[role="menuitem"]'));
  c('菜单项数量 >= 2', items.length >= 2, items.length + ' 项');
  if (items.length >= 2) {
    items[0].focus();
    key(items[0], 'ArrowDown');
    await wait(150);
    c('↓ 移动到下一项', d.activeElement === items[1], blob(d.activeElement));
    key(d.activeElement, 'ArrowUp');
    await wait(150);
    c('↑ 回到上一项', d.activeElement === items[0], blob(d.activeElement));
    key(d.activeElement, 'ArrowUp');
    await wait(150);
    c('↑ 在首项时循环到末项', d.activeElement === items[items.length - 1],
      blob(d.activeElement));
    key(d.activeElement, 'ArrowDown');
    await wait(150);
    c('↓ 在末项时循环到首项', d.activeElement === items[0], blob(d.activeElement));
    key(d.activeElement, 'End');
    await wait(150);
    c('End 跳到末项', d.activeElement === items[items.length - 1], blob(d.activeElement));
    key(d.activeElement, 'Home');
    await wait(150);
    c('Home 跳到首项', d.activeElement === items[0], blob(d.activeElement));
  }

  /* ============ 6. 菜单项 Enter / Space 触发 ============ */
  if (items.length) {
    items[0].focus();
    const before = !!d.querySelector('.notif__review');
    // 只发 keydown：item-head 上有 @keydown.enter，再补一次 click 会把
    // expandInPanel 触发两次（第二次是 toggle 回来），第一版就这么写错了。
    key(items[0], 'Enter');
    await wait(800);
    const after = !!d.querySelector('.notif__review');
    c('菜单项 Enter 触发展开复习视图', !before && after,
      'before=' + before + ' after=' + after);
  }

  /* ============ 7. 下拉：点击外部关闭 ============ */
  const opened = !!d.getElementById('notif-panel');
  const outsideEl = d.querySelector('.sidebar .nav__item');
  if (outsideEl) {
    outsideEl.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    outsideEl.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    outsideEl.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  } else {
    d.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  }
  await wait(700);
  c('点击下拉外部关闭面板', opened && !d.getElementById('notif-panel'),
    'opened=' + opened + ' now=' + !!d.getElementById('notif-panel'));

  /* ============ 8. 下拉：Esc 关闭 + 归还焦点 ============ */
  /* 先 focus 再 click：程序化 .click() 不会移动焦点，而"归还焦点"依赖
     rememberFocus() 记住的那个元素 —— 不先聚焦就测不到真东西。 */
  bell.focus();
  bell.click();
  await wait(900);
  c('重新打开面板', !!d.getElementById('notif-panel'));
  w.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await wait(700);
  c('Esc 关闭通知面板', !d.getElementById('notif-panel'));
  c('Esc 关闭后焦点归还铃铛', d.activeElement === bell,
    blob(d.activeElement) + ' / bell=' + blob(bell));

  /* ============ 9. 用户菜单键盘 ============ */
  const userBtn = d.querySelector('.user-menu');
  if (userBtn) {
    userBtn.click();
    await wait(800);
    const menu = d.getElementById('user-menu');
    c('用户菜单打开', !!menu);
    const mitems = menu ? Array.from(menu.querySelectorAll('[role="menuitem"]')) : [];
    c('用户菜单有菜单项', mitems.length >= 2, mitems.length + ' 项');
    c('用户菜单打开后焦点进入菜单项',
      !!d.activeElement && d.activeElement.getAttribute('role') === 'menuitem',
      blob(d.activeElement));
    if (mitems.length >= 2) {
      mitems[0].focus();
      key(mitems[0], 'ArrowDown');
      await wait(150);
      c('用户菜单 ↓ 移动焦点', d.activeElement === mitems[1], blob(d.activeElement));
    }
    w.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await wait(600);
    c('用户菜单 Esc 关闭', !d.getElementById('user-menu'));
  } else {
    c('找到用户菜单按钮', false, '未找到 .user-menu');
  }

  /* ============ 10. 表格 caption ============ */
  const navQs = Array.from(d.querySelectorAll('.nav__item'))
    .find(n => n.textContent.includes('题目管理'));
  if (navQs) {
    navQs.click();
    await wait(1800);
  }
  const table = d.querySelector('.data-table');
  c('题目表格存在', !!table, navQs ? '已切到题目管理' : '未找到导航项');
  if (table) {
    const cap = table.querySelector('caption');
    c('表格有 <caption>', !!cap, cap ? cap.textContent.trim().slice(0, 24) : '-');
    if (cap) {
      const cs = w.getComputedStyle(cap);
      // 必须是"视觉隐藏但对读屏可见"：display:none 会让读屏也读不到
      c('caption 未被 display:none 隐藏（读屏仍可读）',
        cs.display !== 'none', 'display=' + cs.display);
    }
    c('表格有 aria 名称（caption 或 aria-label）',
      !!cap || table.hasAttribute('aria-label'));
  }

  /* ============ 11. aria-live 播报 ============ */
  const live = d.querySelector('[role="status"][aria-live="polite"]');
  c('存在 aria-live 播报区', !!live, blob(live));

  /* 打勾后应播报。断言实际的 liveRegion 文本，而不是"代码里有那个字符串"。 */
  if (live) {
    live.textContent = '';
    // 明确把面板打开（前面的用例可能已把它关掉/打开），再等菜单项渲染
    if (!d.getElementById('notif-panel')) {
      bell.click();
      await wait(1200);
    }
    c('打勾前通知面板已打开', !!d.getElementById('notif-panel'));
    const firstItem = d.querySelector('.notif__item-head');
    c('面板内有列表项', !!firstItem);
    if (firstItem) {
      firstItem.click();
      // 等复习视图真的渲染出来（.notif__review 是 v-if 控制的）
      const review = await until(() => d.querySelector('.notif__review'), 6000);
      c('点列表项后展开复习视图', !!review,
        review ? 'ok' : ('aria-current=' + firstItem.getAttribute('aria-current')));
      const checkBtn = Array.from(d.querySelectorAll('button'))
        .find(b => b.textContent.trim() === '打勾');
      c('面板内有打勾按钮', !!checkBtn,
        checkBtn ? 'ok' : ('按钮总数=' + d.querySelectorAll('.notif__panel button').length));
      if (checkBtn) {
        checkBtn.click();
        await wait(1400);
        c('打勾后 aria-live 播报了结果',
          /打勾|复习/.test(live.textContent), JSON.stringify(live.textContent.slice(0, 30)));
      }
    }
    // 关掉面板，避免影响后续
    const closeBtn = d.querySelector('#notif-panel button[aria-label="关闭通知面板"]');
    if (closeBtn) { closeBtn.click(); await wait(400); }
  }

  /* 草稿自动保存也应播报（笔记页输入后等防抖） */
  if (live) {
    live.textContent = '';
    const navNotes = Array.from(d.querySelectorAll('.nav__item'))
      .find(n => n.textContent.includes('记事本'));
    if (navNotes) {
      navNotes.click();
      await wait(1600);
      const titleInput = d.querySelector('.notes-editor input[type="text"]')
        || d.querySelector('.notes-editor .input');
      if (titleInput) {
        titleInput.value = (titleInput.value || '') + '测';
        titleInput.dispatchEvent(new Event('input', { bubbles: true }));
        await wait(1500);          // 防抖 500ms + 余量
        c('笔记输入后稿草自动保存并播报',
          /保存/.test(live.textContent), JSON.stringify(live.textContent.slice(0, 30)));
      } else {
        c('找到笔记标题输入框', false, '未找到');
      }
    } else {
      c('找到记事本导航项', false, '未找到');
    }
  }

  const pre = document.createElement('pre');
  pre.id = 'a11y-result';
  pre.textContent = JSON.stringify(results);
  document.body.appendChild(pre);
})().catch(err => {
  /* 探针抛错时也要把已有结果写出来，否则排错时只能看到一个空节点 */
  results.push({ name: '探针运行无异常', ok: false,
                 detail: (err && err.message ? err.message : String(err)) });
  const pre = document.createElement('pre');
  pre.id = 'a11y-result';
  pre.textContent = JSON.stringify(results);
  document.body.appendChild(pre);
});
</script></body></html>"""


def call(method: str, path: str, payload: dict | None = None, timeout: float = 30):
    """对测试服务器发一个请求（绕开系统代理）。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with _opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read()


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def seed() -> int:
    """造数据：一个学科 + 一个大类 + 三道题。

    测试服务器本身**不会**造数据（它只提供 /__redue 这类改库接口），
    所以这里自己发 API 请求 —— 第一版没造数据，通知面板一个菜单项都没有，
    ↑/↓ 那几项等于没测（断言"菜单项 >= 2"直接失败才发现）。
    """
    _status, body = call("POST", "/folders", {"name": "数学", "parent_id": None})
    subject = json.loads(body)["id"]
    _status, body = call("POST", "/folders",
                         {"name": "极限与连续", "parent_id": subject})
    leaf = json.loads(body)["id"]

    made = 0
    for i, stem in enumerate([
        "求极限 lim(x→0) sin(x)/x",
        "洛必达法则的适用条件是什么",
        "求 y=x^3 的导数",
    ]):
        _status, _body = call("POST", "/questions", {
            "folder_id": leaf, "stem": stem,
            "answer": f"答案 {i + 1}", "tags": ["重要极限"],
            "is_starred": i == 0, "sort_order": i,
        })
        made += 1

    # 新题的首条记录到期时间是 now()+3 天，必须回拨才会进今日队列
    call("POST", "/__redue")
    return made


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="a11y_"))
    harness = tmp / "a11y.html"
    harness.write_text(PROBE.replace("__BASE__", BASE), encoding="utf-8")

    # 起一个与 verify_responsive 相同的测试服务器（用同一份 scaffolding）
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import runpy,sys; sys.argv=['x',%r,%r,%r]; "
         "runpy.run_path(r'tools/_serve_for_ui_test.py', run_name='__main__')"
         % (PORT, str(harness), str(tmp / "a11y.db"))],
        cwd=".", stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(60):
            time.sleep(0.5)
            try:
                urllib.request.urlopen(BASE + "/health", timeout=2).read()
                ready = True
                break
            except Exception:
                pass
        if not ready:
            print("测试服务器未就绪")
            return 2

        n = seed()
        print(f"[seed] 已造 {n} 道题并回拨到期时间\n")

        dom = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
             "--force-device-scale-factor=1", "--window-size=1500,1000",
             "--virtual-time-budget=90000", "--dump-dom", BASE + "/__harness"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300).stdout or ""

        # 注意：测试页里可能有**多个** <pre id="a11y-result">（探针在异常路径下
        # 也会插一个空的），所以要取最后一个非空的那个，不能用 re.search 拿第一个。
        raws = re.findall(r'<pre id="a11y-result">(.*?)</pre>', dom, re.S)
        raw = ""
        for candidate in reversed(raws):
            text = (candidate.replace("&quot;", '"').replace("&amp;", "&")
                    .replace("&lt;", "<").replace("&gt;", ">")).strip()
            if text.startswith("["):
                raw = text
                break
        if not raw:
            print(f"未取到断言结果（找到 {len(raws)} 个结果节点）；DOM 尾部：")
            print(dom[-1200:])
            return 2
        rows = json.loads(raw)
        for r in rows:
            print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['name']}"
                  + (f" — {r['detail']}" if r["detail"] else ""))
        failed = [r for r in rows if not r["ok"]]
        print("-" * 74)
        print(f"合计 {len(rows)} 项，通过 {len(rows) - len(failed)}，失败 {len(failed)}")
        for r in failed:
            print(f"  FAILED: {r['name']} — {r['detail']}")
        return 1 if failed else 0
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
