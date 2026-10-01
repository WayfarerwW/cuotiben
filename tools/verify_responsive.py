"""在真实浏览器里按三个断点验证响应式 + 可访问性。

用 --window-size 无法精确控制视口，因此这里用 CDP 风格的简单办法：
在测试页里把被测页面放进 iframe，并显式设置 iframe 宽度来模拟断点。
（iframe 内的 window.innerWidth 就是 iframe 宽度，媒体查询按 iframe 生效。）
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = sys.argv[1] if len(sys.argv) > 1 else "8975"
DB = sys.argv[2] if len(sys.argv) > 2 else str(Path(tempfile.gettempdir()) / "rwd.db")
BASE = f"http://127.0.0.1:{PORT}"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

PROBE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"></head><body>
<script>
const wait = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, ms) {
  const end = Date.now() + (ms || 12000);
  while (Date.now() < end) { try { const v = fn(); if (v) return v; } catch(e){} await wait(150); }
  return null;
}
const results = [];
function c(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
}
function mount(width) {
  return new Promise(async (resolve) => {
    const f = document.createElement('iframe');
    f.style.cssText = 'border:0;height:900px;width:' + width + 'px';
    document.body.appendChild(f);
    // 加时间戳避免 Edge 命中上一次的同名缓存
    f.src = '__BASE__/?t=' + Date.now();
    const app = await until(() => {
      const d = f.contentDocument;
      if (!d) return null;
      const el = d.getElementById('app');
      return el && !el.hasAttribute('v-cloak') ? el : null;
    }, 15000);
    await wait(1500);
    resolve({ f, d: f.contentDocument, app });
  });
}
(async () => {
  // 每个断点都用**全新的 iframe**：断点是在 iframe 的 window 加载时
  // 由 applyBreakpoint() 按 innerWidth 判定的，复用同一个 iframe 改宽度
  // 不会重新判定（iframe 内不会触发 window resize）。

  /* ---- 桌面端 >1200 ---- */
  let { f, d, app } = await mount(1400);
  c('桌面端 侧边栏宽 260px',
    Math.round(d.querySelector('.sidebar').getBoundingClientRect().width) === 260,
    d.querySelector('.sidebar').getBoundingClientRect().width);
  c('桌面端 非折叠态', !app.classList.contains('is-collapsed'), app.className);
  c('桌面端 统计卡片 4 列',
    getComputedStyle(d.querySelector('.stat-grid')).gridTemplateColumns.split(' ').length === 4,
    getComputedStyle(d.querySelector('.stat-grid')).gridTemplateColumns);
  c('桌面端 汉堡菜单隐藏',
    getComputedStyle(d.querySelector('.topbar__hamburger')).display === 'none');
  f.remove();

  /* ---- 平板端 768-1200 ---- */
  ({ f, d, app } = await mount(1000));
  c('平板端 默认折叠（is-collapsed）',
    d.querySelector('.app').classList.contains('is-collapsed'),
    d.querySelector('.app').className);
  c('平板端 侧边栏折叠为 64px',
    Math.round(d.querySelector('.sidebar').getBoundingClientRect().width) === 64,
    d.querySelector('.sidebar').getBoundingClientRect().width);
  c('平板端 内容区让位（margin-left 64px）',
    getComputedStyle(d.querySelector('.main')).marginLeft === '64px',
    getComputedStyle(d.querySelector('.main')).marginLeft);
  c('平板端 统计卡片退为 2 列',
    getComputedStyle(d.querySelector('.stat-grid')).gridTemplateColumns.split(' ').length === 2,
    getComputedStyle(d.querySelector('.stat-grid')).gridTemplateColumns);
  // 用户手动展开后应保持展开
  d.querySelector('.topbar .icon-btn:not(.topbar__hamburger)').click();
  await wait(700);
  c('平板端 用户手动展开后保持展开',
    !d.querySelector('.app').classList.contains('is-collapsed'),
    d.querySelector('.app').className);
  f.remove();

  /* ---- 移动端 <768 ---- */
  ({ f, d, app } = await mount(420));
  c('移动端 汉堡菜单可见',
    getComputedStyle(d.querySelector('.topbar__hamburger')).display !== 'none',
    getComputedStyle(d.querySelector('.topbar__hamburger')).display);
  const sb = d.querySelector('.sidebar');
  c('移动端 侧边栏默认移出屏幕（translateX 负值）',
    sb.getBoundingClientRect().right <= 1, Math.round(sb.getBoundingClientRect().right));
  d.querySelector('.topbar__hamburger').click();
  await wait(900);
  c('移动端 点汉堡打开抽屉',
    d.querySelector('.app').classList.contains('is-drawer-open'),
    d.querySelector('.app').className);
  c('移动端 抽屉遮罩出现', !!d.querySelector('.drawer-mask'));
  // 重新取一次：抽屉是 CSS 过渡移动的，缓动需要时间
  await wait(500);
  const sbAfter = d.querySelector('.sidebar');
  // 注意：无头 + 虚拟时间下 rAF 不推进，CSS transition 可能停在中途，
  // getBoundingClientRect 会读到中间值。因此这里查的是
  // **CSSOM 里最终生效的规则**，而不是正在动画的位置。
  let drawerRule = '';
  for (const sheet of Array.from(d.styleSheets)) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const r of Array.from(rules)) {
      if (r.media && r.media.mediaText.includes('767')) {
        for (const inner of Array.from(r.cssRules)) {
          if (inner.selectorText === '.app.is-drawer-open .sidebar') {
            drawerRule = inner.style.transform;
          }
        }
      }
    }
  }
  // 浏览器会把 0 序列化成 0px，按数值判断而不是字符串全等
  c('移动端 抽屉打开时侧边栏归位（CSS 规则层面）',
    /translateX\(\s*-?0(px)?\s*\)/.test(drawerRule), drawerRule);
  c('移动端 顶部按钮文字隐藏',
    getComputedStyle(d.querySelector('.topbar__actions .btn__label')).display === 'none');
  c('移动端 统计卡片单列',
    getComputedStyle(d.querySelector('.stat-grid')).gridTemplateColumns.split(' ').length === 1,
    getComputedStyle(d.querySelector('.stat-grid')).gridTemplateColumns);
  // 表格转卡片：切到题目页看 thead 是否隐藏
  d.querySelectorAll('.nav__item')[1].click();
  await wait(1600);
  const thead = d.querySelector('.data-table thead');
  c('移动端 表格 head 隐藏（转卡片）',
    !thead || getComputedStyle(thead).display === 'none',
    thead ? getComputedStyle(thead).display : '无表格');

  /* ---- 可访问性（在移动端 iframe 里测，逻辑与宽度无关）---- */
  // Esc 关闭弹窗
  d.querySelector('.topbar__actions .btn--primary').click();
  await wait(800);
  c('可访问性 弹窗打开', !!d.querySelector('.modal'));
  const firstFocus = d.activeElement;
  c('可访问性 弹窗打开后焦点移入弹窗',
    !!(firstFocus && d.querySelector('.modal') && d.querySelector('.modal').contains(firstFocus)),
    firstFocus ? firstFocus.tagName + '.' + (firstFocus.className || '') : 'null');
  d.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await wait(600);
  c('可访问性 Esc 关闭弹窗', !d.querySelector('.modal'));
  f.remove();

  const pre = document.createElement('pre');
  pre.id = 'rwd-result';
  pre.textContent = JSON.stringify(results);
  document.body.appendChild(pre);
})();
</script></body></html>"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="rwd_"))
    harness = tmp / "rwd.html"
    harness.write_text(PROBE.replace("__BASE__", BASE), encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import runpy,sys; sys.argv=['x',%r,%r,%r]; "
         "runpy.run_path(r'tools/_serve_for_ui_test.py', run_name='__main__')"
         % (PORT, str(harness), DB)],
        cwd=".", stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        ok = False
        for _ in range(40):
            time.sleep(0.5)
            try:
                urllib.request.urlopen(BASE + "/health", timeout=2).read()
                ok = True
                break
            except Exception:
                pass
        if not ok:
            print("服务未就绪")
            return 2

        dom = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
             "--force-device-scale-factor=1", "--window-size=1600,1000",
             "--virtual-time-budget=60000", "--dump-dom", BASE + "/__harness"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300).stdout or ""

        m = re.search(r'<pre id="rwd-result">(.*?)</pre>', dom, re.S)
        if not m:
            print("未取到结果；DOM 尾部：")
            print(dom[-900:])
            return 2
        raw = (m.group(1).replace("&quot;", '"').replace("&amp;", "&")
               .replace("&lt;", "<").replace("&gt;", ">"))
        rows = json.loads(raw)
        passed = [r for r in rows if r["ok"]]
        failed = [r for r in rows if not r["ok"]]
        for r in rows:
            print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['name']}"
                  + (f" — {r['detail']}" if r["detail"] else ""))
        print("-" * 74)
        print(f"合计 {len(rows)} 项，通过 {len(passed)}，失败 {len(failed)}")
        for r in failed:
            print(f"  FAILED: {r['name']} — {r['detail']}")
        return 1 if failed else 0
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
