"""在真实浏览器里验证 app.js：渲染、页面切换、交互。

为什么用 iframe：--dump-dom 只能拿到文档加载完成时的快照，而本步要验证的是
**交互后**的状态（点通知铃铛、切换页面、输入触发草稿）。把被测页面放进同源
iframe，就能在一个脚本里反复操作它并断言，最后把结果写进本页 DOM 供读取。

不改动 app/static 下的任何文件 —— 测试脚本是运行时注入的。

前置：另起一个 tools/_serve_for_ui_test.py，它把静态前端、后端 API 与
本测试页放在同一个 origin（真实部署时前端本来就由后端提供，同源）。
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def seed(base: str) -> dict:
    """通过真实 HTTP 接口准备数据（幂等）。"""
    base = base.rstrip("/")  # 末尾带 / 会拼出 //folders，被当成静态路径

    def call(method: str, path: str, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        url = base + path
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8")
                if os.environ.get("UI_TEST_DEBUG") == "1":
                    print(f"    [call] {method} {url} -> {resp.status} "
                          f"ctype={resp.headers.get('content-type')} "
                          f"len={len(body)}", flush=True)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"{method} {path} -> HTTP {exc.code}: {detail[:300]}"
            ) from exc
        if not body.strip():
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{method} {path} 返回了非 JSON 内容: {body[:200]!r}"
            ) from exc

    tree = call("GET", "/folders/tree")
    if not any(s["name"] == "高等数学" for s in tree):
        call("POST", "/folders", {"name": "高等数学"})
    tree = call("GET", "/folders/tree")
    math = next(s for s in tree if s["name"] == "高等数学")

    cats = {}
    for name in ("极限与连续", "导数与微分"):
        found = next((c for c in math.get("children", []) if c["name"] == name), None)
        if not found:
            found = call("POST", "/folders", {"name": name, "parent_id": math["id"]})
        cats[name] = found

    if not call("GET", "/questions"):
        call("POST", "/questions", {
            "folder_id": cats["极限与连续"]["id"],
            "stem": "求 lim(x→0) sin(x)/x",
            "answer": "1",
            "tags": ["极限", "重要极限"],
            "is_starred": True,
        })
        call("POST", "/questions", {
            "folder_id": cats["极限与连续"]["id"],
            "stem": "洛必达法则的适用条件是什么",
            "answer": "0/0 或 无穷/无穷 型，且导数之比的极限存在",
            "tags": ["极限", "洛必达"],
        })
        q3 = call("POST", "/questions", {
            "folder_id": cats["导数与微分"]["id"],
            "stem": "求 y=x^3 的导数",
            "answer": "y'=3x^2",
            "tags": ["导数"],
        })
        call("POST", "/questions/%d/mastery" % q3["id"],
             {"mastery_status": "mastered"})

    if not call("GET", "/notes"):
        call("POST", "/notes", {"title": "极限笔记", "content": "洛必达法则的适用条件"})
        call("POST", "/notes", {"title": "导数纪要", "content": "链式法则容易漏乘"})

    return {
        "subjects": len(call("GET", "/folders/tree")),
        "questions": len(call("GET", "/questions")),
        "notes": len(call("GET", "/notes")),
        "tags": len(call("GET", "/tags")),
        "reviewQueue": len(call("GET", "/review/today")),
    }

INJECTED = r"""
<script>
const RESULTS = [];
function check(name, ok, detail) {
  RESULTS.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
}
function dump() {
  const pre = document.createElement('pre');
  pre.id = 'test-results';
  pre.textContent = JSON.stringify(RESULTS);
  document.body.appendChild(pre);
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// 在 iframe 文档里按选择器或可见文本找元素
function inDoc(frame, selector, text) {
  const doc = frame.contentDocument;
  if (!doc) return null;
  const nodes = Array.from(doc.querySelectorAll(selector));
  if (text === undefined) return nodes[0] || null;
  return nodes.find((n) => (n.textContent || '').includes(text)) || null;
}

function allInDoc(frame, selector) {
  const doc = frame.contentDocument;
  return doc ? Array.from(doc.querySelectorAll(selector)) : [];
}

function setHash(frame, hash) {
  frame.contentWindow.location.hash = hash;
}

async function run() {
  check('步骤：脚本开始', true, '');
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'width:1440px;height:1000px;border:0';
  document.body.appendChild(iframe);

  // 先把 onload 挂上再设 src：顺序反了会丢掉 load 事件
  await new Promise((resolve) => {
    let settled = false;
    const done = () => { if (!settled) { settled = true; resolve(); } };
    iframe.onload = done;
    iframe.onerror = done;
    iframe.src = window.__TARGET__;
    // 兜底：有的无头环境不派发 iframe load
    const timer = setInterval(() => {
      try {
        const d = iframe.contentDocument;
        if (d && d.readyState === 'complete' && d.getElementById('app')) {
          clearInterval(timer);
          done();
        }
      } catch (e) { /* 还没就绪 */ }
    }, 200);
    setTimeout(() => { clearInterval(timer); done(); }, 15000);
  });
  check('步骤：iframe 已加载', true,
    iframe.contentDocument ? iframe.contentDocument.readyState : 'no-doc');
  await wait(2000); // 等 Vue 挂载 + 首次数据加载
  check('步骤：等待完成', true,
    iframe.contentDocument && iframe.contentDocument.getElementById('app')
      ? (iframe.contentDocument.getElementById('app').className || '(无 class)')
      : 'no-app');

  const doc = iframe.contentDocument;
  const win = iframe.contentWindow;

  /* ---------------- 0. 基础：Vue 是否真的接管了 ---------------- */
  check('Vue 已加载', typeof win.Vue !== 'undefined', typeof win.Vue);
  check('API 已加载', typeof win.API !== 'undefined', typeof win.API);
  check('模板已编译（无残留 {{ }}）',
    !/\{\{[^}]*\}\}/.test(doc.body.innerHTML),
    (doc.body.innerHTML.match(/\{\{[^}]*\}\}/) || [''])[0]);
  check('v-cloak 已移除（应用可见）',
    !doc.getElementById('app').hasAttribute('v-cloak'));
  check('无 Vue 渲染错误覆盖层', !doc.querySelector('.placeholder--error'),
    (doc.querySelector('.placeholder--error') || {}).textContent);

  /* ---------------- 1. 全局外壳 ---------------- */
  check('侧边栏 6 个功能导航',
    allInDoc(iframe, '.nav__item').length === 6,
    allInDoc(iframe, '.nav__item').length);
  check('导航标签正确',
    allInDoc(iframe, '.nav__label').map((n) => n.textContent.trim()).join(',') ===
      '首页,题目管理,今日复习,记事本,统计分析,设置',
    allInDoc(iframe, '.nav__label').map((n) => n.textContent.trim()).join(','));
  check('顶部含搜索框', !!inDoc(iframe, '.search__input'));
  check('顶部含导出 PDF 按钮', !!inDoc(iframe, '.topbar__actions button', '导出 PDF'));
  check('顶部含新增题目按钮', !!inDoc(iframe, '.topbar__actions button', '新增题目'));
  check('顶部含通知铃铛', !!inDoc(iframe, '#btn-bell'));

  /* ---------------- 2. 文件夹树 ---------------- */
  const treeLabels = allInDoc(iframe, '.tree__label').map((n) => n.textContent.trim());
  check('文件夹树渲染出学科', treeLabels.includes('高等数学'), treeLabels.join(','));
  check('文件夹树展开后有二级大类',
    treeLabels.includes('极限与连续') && treeLabels.includes('导数与微分'),
    treeLabels.join(','));
  check('文件夹节点显示题目计数',
    allInDoc(iframe, '.tree__count').length >= 3,
    allInDoc(iframe, '.tree__count').map((n) => n.textContent.trim()).join(','));

  /* ---------------- 3. 首页 ---------------- */
  check('首页渲染统计卡片', allInDoc(iframe, '.stat-card__value').length === 4,
    allInDoc(iframe, '.stat-card__value').map((n) => n.textContent.trim()).join(','));
  check('首页统计卡片有真实数值（非占位符 —）',
    allInDoc(iframe, '.stat-card__value').every((n) => n.textContent.trim() !== '—'),
    allInDoc(iframe, '.stat-card__value').map((n) => n.textContent.trim()).join(','));
  check('首页有今日复习推荐区',
    !!inDoc(iframe, '.section-title', '今日复习推荐'));

  /* ---------------- 4. 通知面板：面板内展开复习视图（不跳页） ---------------- */
  const bell = inDoc(iframe, '#btn-bell');
  const pageBefore = doc.title;
  bell.click();
  await wait(700);
  check('点击铃铛后面板展开', !!inDoc(iframe, '#notif-panel'));
  check('打开面板没有跳页（标题未变）', doc.title === pageBefore, doc.title);
  const notifItems = allInDoc(iframe, '.notif__item');
  check('面板内渲染待复习列表', notifItems.length > 0, notifItems.length + ' 条');

  if (notifItems.length) {
    const head = notifItems[0].querySelector('.notif__item-head');
    head.click();
    await wait(400);
    check('点击列表项在面板内展开复习视图（未跳页）',
      !!inDoc(iframe, '.notif__review') && doc.title === pageBefore);
    check('内嵌视图含打勾按钮', !!inDoc(iframe, '.notif__review button', '打勾'));
    check('内嵌视图含上一题/下一题',
      !!inDoc(iframe, '.notif__review button', '上一题') &&
      !!inDoc(iframe, '.notif__review button', '下一题'));

    const answerBtn = inDoc(iframe, '.notif__review button', '展开答案');
    if (answerBtn) {
      answerBtn.click();
      await wait(400);
      check('展开答案后出现答案内容', !!inDoc(iframe, '.notif__review .review__answer'));
    } else {
      check('展开答案按钮存在', false, '未找到');
    }

    // 打勾：应从队列移除并刷新角标
    const before = allInDoc(iframe, '.notif__item').length;
    const badgeBefore = (inDoc(iframe, '.bell__badge') || {}).textContent || '';
    const checkBtn = inDoc(iframe, '.notif__review button', '打勾');
    checkBtn.click();
    await wait(1200);
    const after = allInDoc(iframe, '.notif__item').length;
    const badgeAfter = (inDoc(iframe, '.bell__badge') || {}).textContent || '';
    check('打勾后从队列移除', after === before - 1, `${before} -> ${after}`);
    check('打勾后角标刷新', badgeBefore !== badgeAfter,
      `"${badgeBefore}" -> "${badgeAfter}"`);
    check('打勾后出现成功 Toast', !!inDoc(iframe, '.toast--success'));
  } else {
    check('待复习列表非空（需先造逾期数据）', false, '0 条');
  }

  // 关闭面板
  const closeBtn = inDoc(iframe, '#notif-panel button[aria-label="关闭通知面板"]');
  if (closeBtn) closeBtn.click();
  await wait(300);

  /* ---------------- 5. 页面切换（组件内状态，不用 vue-router） ---------------- */
  for (const [label, expect] of [
    ['题目管理', '题目管理'],
    ['今日复习', '今日复习'],
    ['记事本', '记事本'],
    ['统计分析', '统计分析'],
    ['设置', '设置'],
    ['首页', '首页概览'],
  ]) {
    const link = inDoc(iframe, '.nav__item', label);
    link.click();
    await wait(900);
    const title = (inDoc(iframe, '.page-title') || {}).textContent || '';
    check(`切换到「${label}」页标题正确`, title.trim() === expect, title.trim());
  }

  /* ---------------- 6. 题目列表与筛选 ---------------- */
  inDoc(iframe, '.nav__item', '题目管理').click();
  await wait(900);
  const rows = allInDoc(iframe, '.data-table tbody tr');
  check('题目表格渲染出数据行', rows.length === 3, rows.length + ' 行');
  check('表格含标签',
    allInDoc(iframe, '.data-table .tag').length > 0,
    allInDoc(iframe, '.data-table .tag').length);
  check('表格含正误状态徽章', allInDoc(iframe, '.data-table .status').length === 3,
    allInDoc(iframe, '.data-table .status').length);
  check('表格含重点星标', allInDoc(iframe, '.data-table .star').length === 3);

  const segs = allInDoc(iframe, '.segmented__item');
  check('筛选分段控件 4 项', segs.length === 4, segs.length);
  const starredSeg = segs.find((s) => s.textContent.trim() === '重点');
  starredSeg.click();
  await wait(300);
  check('筛选「重点」后只剩 1 行',
    allInDoc(iframe, '.data-table tbody tr').length === 1,
    allInDoc(iframe, '.data-table tbody tr').length);
  const masteredSeg = segs.find((s) => s.textContent.trim() === '已掌握');
  masteredSeg.click();
  await wait(300);
  check('筛选「已掌握」后只剩 1 行',
    allInDoc(iframe, '.data-table tbody tr').length === 1,
    allInDoc(iframe, '.data-table tbody tr').length);
  segs.find((s) => s.textContent.trim() === '全部').click();
  await wait(300);
  check('筛选「全部」恢复 3 行',
    allInDoc(iframe, '.data-table tbody tr').length === 3,
    allInDoc(iframe, '.data-table tbody tr').length);

  /* ---------------- 7. 点击二级文件夹 → 切到题目页并筛该文件夹 ---------------- */
  setHash(iframe, '#/home');
  await wait(1200);
  const catRows = allInDoc(iframe, '.tree__children .tree__row');
  check('二级文件夹可点击的行数', catRows.length === 2,
    catRows.map((r) => r.textContent.trim()).join('|'));
  const catRow = catRows.find((r) => r.textContent.includes('导数与微分'));
  check('找到「导数与微分」节点', !!catRow,
    catRows.map((r) => r.textContent.trim()).join('|'));
  if (catRow) {
    catRow.click();
    await wait(1500);
    const titleNow = (inDoc(iframe, '.page-title') || {}).textContent || '';
    const toolbarNow = (inDoc(iframe, '.toolbar .text-aux') || {}).textContent || '';
    const rowsNow = allInDoc(iframe, '.data-table tbody tr').length;
    check('点击二级文件夹跳到题目页', titleNow.includes('题目管理'), titleNow);
    check('并且筛出该文件夹下的题目（1 行）', rowsNow === 1,
      `行数=${rowsNow} 工具栏="${toolbarNow.replace(/\s+/g, ' ').trim()}"`);
    check('显示当前文件夹名', toolbarNow.includes('导数与微分'),
      toolbarNow.replace(/\s+/g, ' ').trim());
  }

  /* ---------------- 8. 复习页 ---------------- */
  // 上一步在面板里打勾会消耗队列；这里刷新回待复习，再切页，
  // loadPage('review') 就会拉到非空队列。
  if (window.__REDUE__) {
    await fetch(window.__REDUE__, { method: 'POST' });
  }
  inDoc(iframe, '.nav__item', '今日复习').click();
  await wait(1500);
  check('复习页有进度条', !!inDoc(iframe, '.review-progress .progress__bar'));
  const cards = allInDoc(iframe, '.review-card');
  check('复习页渲染复习卡片', cards.length > 0, cards.length + ' 张');
  if (cards.length) {
    // 打勾评价已由 4 档收敛为 2 档（requirements 2.14）
    const rateBtns = allInDoc(iframe, '.review-card__actions button')
      .filter(b => /未完全掌握|已掌握/.test(b.textContent || ''));
    check('**复习卡片含 2 档评价按钮（未完全掌握 / 已掌握）**',
      rateBtns.length === 2, rateBtns.length + ' 个');
    check('评价按钮文案正确',
      rateBtns.some(b => (b.textContent || '').includes('未完全掌握')) &&
      rateBtns.some(b => (b.textContent || '').includes('已掌握')),
      rateBtns.map(b => (b.textContent || '').trim()).join(' / '));
    check('不再出现旧的 4 档文案',
      !/完全不会|有点模糊|基本掌握/.test(
        allInDoc(iframe, '.review-card__actions').map(
          e => e.textContent || '').join('')),
      '旧档位已移除');
    const showBtn = inDoc(iframe, '.review-card__actions', '显示答案') ||
      inDoc(iframe, '.review-card button', '显示答案');
    if (showBtn) {
      showBtn.click();
      await wait(400);
      check('复习页展开答案', !!inDoc(iframe, '.review-card .review__answer'));
    }
    const cardBefore = allInDoc(iframe, '.review-card').length;
    const doneBtn = allInDoc(iframe, '.review-card__actions button')
      .find((b) => b.textContent.trim() === '已掌握');
    doneBtn.click();
    await wait(1200);
    check('复习页打勾后卡片减少',
      allInDoc(iframe, '.review-card').length === cardBefore - 1,
      `${cardBefore} -> ${allInDoc(iframe, '.review-card').length}`);
  }

  /* ---------------- 9. 记事本：分栏 + 草稿 ---------------- */
  win.localStorage.clear();
  inDoc(iframe, '.nav__item', '记事本').click();
  await wait(1200);
  check('记事本左右分栏存在', !!inDoc(iframe, '.notes-layout'));
  check('记事本左侧列表有笔记',
    allInDoc(iframe, '.notes-item').length >= 2,
    allInDoc(iframe, '.notes-item').length + ' 篇（每次运行会新增，只要求 >= 2）');
  const notesBefore = allInDoc(iframe, '.notes-item').length;
  check('记事本右侧有标题与内容输入',
    !!inDoc(iframe, '.notes-editor__title') && !!inDoc(iframe, '.notes-editor__content'));

  // 新建 → 输入 → 等 500ms 防抖 → 断言 localStorage 草稿
  inDoc(iframe, '.notes-list button', '新建笔记').click();
  await wait(400);
  const titleInput = inDoc(iframe, '.notes-editor__title');
  const contentInput = inDoc(iframe, '.notes-editor__content');
  titleInput.value = '草稿测试标题';
  titleInput.dispatchEvent(new win.Event('input', { bubbles: true }));
  contentInput.value = '草稿测试内容';
  contentInput.dispatchEvent(new win.Event('input', { bubbles: true }));

  // 防抖 500ms 内不应落盘
  await wait(200);
  const early = win.localStorage.getItem('draft_note_new');
  check('防抖 500ms 内不写草稿', early === null, String(early));

  await wait(700);
  const saved = win.localStorage.getItem('draft_note_new');
  check('500ms 后草稿写入 draft_note_new', !!saved, String(saved).slice(0, 60));
  let parsed = null;
  try { parsed = JSON.parse(saved || '{}'); } catch (e) { /* 忽略 */ }
  check('草稿内容正确',
    parsed && parsed.title === '草稿测试标题' && parsed.content === '草稿测试内容',
    saved);
  check('显示"已自动保存"提示',
    ((inDoc(iframe, '.notes-editor__head .text-aux') || {}).textContent || '')
      .includes('已自动保存'),
    (inDoc(iframe, '.notes-editor__head .text-aux') || {}).textContent);

  // 保存 → 草稿应被清除
  inDoc(iframe, '.notes-editor button', '保存').click();
  await wait(1200);
  check('保存后草稿被清除',
    win.localStorage.getItem('draft_note_new') === null,
    String(win.localStorage.getItem('draft_note_new')));
  check('保存后列表多出一篇',
    allInDoc(iframe, '.notes-item').length === notesBefore + 1,
    `${notesBefore} -> ${allInDoc(iframe, '.notes-item').length}`);

  // 有草稿时进入页面应弹出恢复提示
  win.localStorage.setItem('draft_note_new', JSON.stringify({
    title: '未保存的草稿', content: '这是草稿内容', at: new Date().toISOString(),
  }));
  inDoc(iframe, '.notes-list button', '新建笔记').click();
  await wait(500);
  check('检测到草稿时显示恢复提示条', !!inDoc(iframe, '.draft-banner'));
  const restoreBtn = inDoc(iframe, '.draft-banner button', '恢复');
  if (restoreBtn) {
    restoreBtn.click();
    await wait(400);
    check('点「恢复」把草稿内容填回编辑器',
      inDoc(iframe, '.notes-editor__title').value === '未保存的草稿',
      inDoc(iframe, '.notes-editor__title').value);
  }

  /* ---------------- 10. 设置页 ---------------- */
  // 先恢复默认，避免上一次运行把 intervals 改掉导致断言不稳定
  await win.API.updateSettings({ intervals: null, backfill_limit: null, backfill_reset_days: null });
  inDoc(iframe, '.nav__item', '设置').click();
  await wait(1200);
  const intervalInputs = allInDoc(iframe, '.interval-field input');
  check('设置页渲染 4 个间隔输入', intervalInputs.length === 4, intervalInputs.length);
  check('间隔初值来自 /settings',
    intervalInputs.map((i) => i.value).join(',') === '3,7,15,30',
    intervalInputs.map((i) => i.value).join(','));
  check('含补卡上限输入', !!inDoc(iframe, '#set-limit'));
  check('含积压阈值输入', !!inDoc(iframe, '#set-days'));

  intervalInputs[0].value = '5';
  intervalInputs[0].dispatchEvent(new win.Event('input', { bubbles: true }));
  await wait(200);
  inDoc(iframe, '.settings-actions button', '保存设置').click();
  await wait(1200);
  check('保存设置后出现成功 Toast', !!inDoc(iframe, '.toast--success'));

  /* ---------------- 11. 数据说明页 ---------------- */
  inDoc(iframe, '.dropdown__item, .user-menu').click();
  await wait(300);
  const dataLink = inDoc(iframe, '.dropdown__item', '数据说明');
  if (dataLink) {
    dataLink.click();
    await wait(1000);
    check('数据说明页显示数据库路径',
      ((inDoc(iframe, '.kv-list .mono') || {}).textContent || '').includes('.db'),
      (inDoc(iframe, '.kv-list .mono') || {}).textContent);
    check('数据说明页列出草稿占用',
      !!inDoc(iframe, '.section-title', '草稿占用'));
  } else {
    check('用户菜单含「数据说明」入口', false, '未找到');
  }

  dump();
}

window.addEventListener('load', () => {
  run().catch((err) => {
    check('测试脚本自身异常', false, err && err.message ? err.message : String(err));
    dump();
  });
});
</script>
"""


def force_due(db_path: Path) -> int:
    """把复习记录回拨成逾期/今日到期，好让复习队列非空。

    时间无法通过接口伪造（这是有意的设计），所以这里直接改库 ——
    与 tools/curl_review.ps1 的做法一致。只动临时库。
    """
    import sqlite3
    from datetime import UTC, datetime, timedelta

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        rows = cur.execute(
            "SELECT id FROM review_records WHERE deleted_at IS NULL ORDER BY question_id"
        ).fetchall()
        offsets = [5, 20, 0]  # 逾期 5 天 / 逾期 20 天（积压）/ 今日到期
        now = datetime.now(UTC)
        for i, (rid,) in enumerate(rows):
            off = offsets[i] if i < len(offsets) else 0
            target = now - timedelta(days=off, hours=1)
            cur.execute("UPDATE review_records SET next_review_at=? WHERE id=?",
                        (target.isoformat(), rid))
        con.commit()
        return len(rows)
    finally:
        con.close()


def build_harness(target: str) -> str:
    redue = target.rstrip("/") + "/__redue"
    injected = (INJECTED
                .replace("window.__TARGET__", json.dumps(target))
                .replace("window.__REDUE__", json.dumps(redue)))
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>ui-harness</title></head><body>" + injected + "</body></html>"
    )


#: 第二阶段专用：验证"学科下新建大类"这个 UI 入口。
#:
#: 为什么必须有这一段：原来**整个前端没有创建大类的入口**
#: （只有 promptNewSubject，不带 parent_id，只能建 level 1 学科）。
#: 而题目只能挂二级大类（requirements 2.2 / 3.10），于是用户建完学科
#: 就再也建不出大类、选不到所属大类、录不进任何题目 —— 录题流程整体断裂。
#:
#: 这类缺陷**后端接口测试永远发现不了**：POST /folders 一直支持 parent_id。
#: 只有真的去点界面上的按钮才测得出来，所以断言必须落在 DOM 上。
CATEGORY_PHASE = r"""
<script>
const RESULTS = [];
function check(name, ok, detail) {
  RESULTS.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'width:1440px;height:1000px;border:0';
  document.body.appendChild(iframe);

  // 让页面处于"学科下没有大类"的状态：先删掉已有大类
  const all = await fetch('/folders/tree').then((r) => r.json());
  const subjects = all || [];
  check('前置：存在至少一个学科', subjects.length >= 1, subjects.length);
  for (const s of subjects) {
    for (const c of (s.children || [])) {
      await fetch('/folders/' + c.id + '?force=true', { method: 'DELETE' });
    }
  }

  await new Promise((resolve) => {
    let settled = false;
    const done = () => { if (!settled) { settled = true; resolve(); } };
    iframe.onload = done;
    iframe.src = window.__TARGET__ + '?nopoll=1';
    setTimeout(done, 15000);
  });
  await wait(2500);
  const doc = iframe.contentDocument;
  const win = iframe.contentWindow;

  const subject = subjects[0];

  /* ---- 1. 学科行必须有「新建大类」按钮 ---- */
  const addBtn = doc.querySelector('.tree__add');
  check('学科行有「新建大类」按钮', !!addBtn,
    addBtn ? addBtn.getAttribute('aria-label') : '找不到 .tree__add');
  check('按钮的 aria-label 指明了所属学科',
    !!addBtn && (addBtn.getAttribute('aria-label') || '').includes(subject.name),
    addBtn ? addBtn.getAttribute('aria-label') : '-');

  /* ---- 2. 没有大类时给出指引（否则用户不知道下一步做什么）---- */
  const hint = doc.querySelector('.tree__hint');
  check('学科下没有大类时显示指引', !!hint,
    hint ? hint.textContent.trim() : '找不到 .tree__hint');

  /* ---- 3. 点按钮真的能建出大类（并带上正确的 parent_id）---- */
  win.prompt = () => '极限与连续';        // 无头环境里 prompt 不出对话框，注入输入
  let created = null;
  const origCreate = win.API.createFolder;
  win.API.createFolder = function (data, opts) {
    created = data;
    return origCreate.call(win.API, data, opts);
  };
  if (addBtn) { addBtn.click(); }
  await wait(2500);

  check('点按钮时把 parent_id 传成了该学科',
    !!created && created.parent_id === subject.id,
    JSON.stringify(created));
  check('新建大类的名字取自输入', !!created && created.name === '极限与连续',
    created ? created.name : '-');

  const tree2 = await fetch('/folders/tree').then((r) => r.json());
  const kids = (tree2.find((s) => s.id === subject.id) || {}).children || [];
  check('服务端确实多出了该大类',
    kids.some((c) => c.name === '极限与连续'),
    JSON.stringify(kids.map((c) => c.name)));

  /* ---- 4. 新建后能直接录题：下拉里出现可选项 ---- */
  const addQ = Array.from(doc.querySelectorAll('button'))
    .find((b) => b.textContent.trim().includes('新增题目'));
  if (addQ) { addQ.click(); }
  await wait(1500);
  const sel = doc.getElementById('q-folder');
  const usable = sel ? Array.from(sel.options).filter((o) => !o.disabled) : [];
  check('题目弹窗的大类下拉出现可选项', usable.length >= 1,
    sel ? Array.from(sel.options).map((o) => o.textContent.trim()).join('|') : '无下拉');
  check('可选项就是刚建的大类',
    usable.some((o) => o.textContent.includes('极限与连续')),
    usable.map((o) => o.textContent.trim()).join('|'));
  check('有可选项时不再显示"还没有大类"提示',
    !doc.getElementById('q-folder-empty'));

  /* ---- 5. 收尾：用这个大类真建一道题（端到端打通）---- */
  let qStatus = null;
  if (sel && usable.length) {
    sel.value = usable[0].value;
    sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    await wait(300);
    const stem = doc.getElementById('q-stem');
    const setter = Object.getOwnPropertyDescriptor(
      win.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(stem, '端到端回归题');
    stem.dispatchEvent(new win.Event('input', { bubbles: true }));
    await wait(300);
    const saveBtn = Array.from(doc.querySelectorAll('.modal button'))
      .find((b) => /保存|创建/.test(b.textContent));
    if (saveBtn) { saveBtn.click(); }
    await wait(2500);
    const qs = await fetch('/questions').then((r) => r.json());
    qStatus = Array.isArray(qs) ? qs.length : qs;
    check('用新建的大类成功录入一道题',
      Array.isArray(qs) && qs.some((q) => q.stem === '端到端回归题'),
      '题目数=' + JSON.stringify(qStatus));
  }

  const pre = document.createElement('pre');
  pre.id = 'cat-results';
  pre.textContent = JSON.stringify(RESULTS);
  document.body.appendChild(pre);
})().catch((e) => {
  RESULTS.push({ name: '第二阶段运行无异常', ok: false,
                 detail: (e && e.message ? e.message : String(e)) });
  const pre = document.createElement('pre');
  pre.id = 'cat-results';
  pre.textContent = JSON.stringify(RESULTS);
  document.body.appendChild(pre);
});
</script>
"""


def run_category_entry_phase(target: str) -> list[dict]:
    """第二阶段：验证"学科下新建大类"的 UI 入口（回归用）。"""
    out_dir = Path.home() / "AppData/Local/Temp/cuotiben_uiharness"
    out_dir.mkdir(parents=True, exist_ok=True)
    harness = out_dir / "cat_harness.html"
    harness.write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>cat-harness</title></head><body>"
        + CATEGORY_PHASE.replace("window.__TARGET__", json.dumps(target))
        + "</body></html>", encoding="utf-8")
    url = target.rstrip("/") + "/__harness2"
    try:
        proc = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--force-device-scale-factor=1", "--window-size=1500,1050",
             "--virtual-time-budget=60000", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        dom = proc.stdout or ""
        m = re.search(r'<pre id="cat-results">(.*?)</pre>', dom, re.S)
        if not m:
            return [{"name": "第二阶段（大类入口）取到结果", "ok": False,
                     "detail": "未取到 cat-results；DOM 尾部：" + dom[-400:]}]
        raw = (m.group(1).replace("&quot;", '"').replace("&amp;", "&")
               .replace("&lt;", "<").replace("&gt;", ">"))
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return [{"name": "第二阶段（大类入口）运行", "ok": False,
                 "detail": f"{type(exc).__name__}: {exc}"}]


#: 第三阶段：题干/答案插图（requirements 2.3）。
#:
#: 这一段要真的走完"选文件 -> 上传 -> 缩略图 -> 保存 -> 重新打开还在"，
#: 因为原来的缺陷就是**前端连上传控件都没有**：后端 POST /upload/image 和
#: api.js 的 uploadImage() 都已实现，但 app.js 从未调用过 ——
#: 只测接口的用例永远发现不了"界面上没有入口"。
IMAGE_PHASE = r"""
<script>
const RESULTS = [];
function check(name, ok, detail) {
  RESULTS.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'width:1440px;height:1100px;border:0';
  document.body.appendChild(iframe);
  await new Promise((resolve) => {
    let settled = false;
    const done = () => { if (!settled) { settled = true; resolve(); } };
    iframe.onload = done;
    iframe.src = window.__TARGET__ + '?nopoll=1';
    setTimeout(done, 15000);
  });
  await wait(2500);
  const doc = iframe.contentDocument;
  const win = iframe.contentWindow;

  /* ---- 前置：必须有一个大类，否则保存会被拦下 ---- */
  const tree = await fetch('/folders/tree').then((r) => r.json());
  let leaf = null;
  for (const s of (tree || [])) { for (const c of (s.children || [])) { leaf = c; } }
  if (!leaf) {
    const s0 = (tree || [])[0];
    if (s0) {
      leaf = await fetch('/folders', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: '插图测试大类', parent_id: s0.id }) })
        .then((r) => r.json());
    }
  }
  check('前置：存在可用于录题的大类', !!leaf, leaf ? leaf.name : '无');

  /* ---- 1. 弹窗里必须有文件输入与"插入图片"按钮 ---- */
  const addBtn = Array.from(doc.querySelectorAll('button'))
    .find((b) => b.textContent.trim().includes('新增题目'));
  if (addBtn) { addBtn.click(); }
  await wait(1500);

  const fileInputs = Array.from(doc.querySelectorAll('input[type="file"]'));
  check('题目弹窗里有文件选择控件', fileInputs.length >= 2, fileInputs.length);
  const imgBtns = Array.from(doc.querySelectorAll('.img-field button'))
    .filter((b) => b.textContent.includes('插入图片'));
  check('题干与答案各有「插入图片」按钮', imgBtns.length >= 2, imgBtns.length);
  check('文件控件接受 HEIC（需求 2.6）',
    fileInputs.some((i) => (i.getAttribute('accept') || '').includes('heic')),
    fileInputs[0] ? fileInputs[0].getAttribute('accept') : '-');

  /* ---- 2. 造一张真 JPEG，走完上传链路 ---- */
  const canvas = doc.createElement('canvas');
  canvas.width = 40; canvas.height = 30;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#3366cc'; ctx.fillRect(0, 0, 40, 30);
  const blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.9));
  const file = new win.File([blob], 'diag.jpg', { type: 'image/jpeg' });

  const stemInput = fileInputs[0];
  const dt = new win.DataTransfer();
  dt.items.add(file);
  stemInput.files = dt.files;
  stemInput.dispatchEvent(new win.Event('change', { bubbles: true }));
  await wait(4000);   // 等上传 + 压缩

  const stemImgs = doc.querySelectorAll('.img-list img');
  check('选文件后出现缩略图', stemImgs.length >= 1, stemImgs.length);
  check('缩略图 src 指向上传结果',
    stemImgs.length > 0 && /uploads\//.test(stemImgs[0].getAttribute('src') || ''),
    stemImgs.length ? stemImgs[0].getAttribute('src') : '-');

  /* ---- 3. 保存后用接口核对 kind 落对 ---- */
  const sel = doc.getElementById('q-folder');
  if (sel && leaf) {
    sel.value = String(leaf.id);
    sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    await wait(300);
  }
  const stemTa = doc.getElementById('q-stem');
  const setter = Object.getOwnPropertyDescriptor(
    win.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(stemTa, '带图题干');
  stemTa.dispatchEvent(new win.Event('input', { bubbles: true }));
  await wait(300);
  const saveBtn = Array.from(doc.querySelectorAll('.modal button'))
    .find((b) => /保存|创建/.test(b.textContent));
  if (saveBtn) { saveBtn.click(); }
  await wait(3000);

  const qs = await fetch('/questions').then((r) => r.json());
  const created = (qs || []).find((q) => q.stem === '带图题干');
  check('带图题目保存成功', !!created, created ? ('id=' + created.id) : '未找到');
  if (created) {
    check('题干图的 kind 落成 stem',
      created.images.some((i) => i.kind === 'stem'), JSON.stringify(created.images.map((i) => i.kind)));
    check('上传的图确实挂在题目上',
      created.images.length >= 1, created.images.length);

    /* ---- 回归：渲染层不能出现 src="undefined" ----
       QuestionImageOut 的字段是 file_path；模板里若写成 img.url 会得到
       undefined，图片区渲染成空白、控制台还不报错，极易漏掉。
       注意：题目表格**本来就不渲染缩略图**（题干预览只有文字），
       所以这里只断言"没有 undefined"，不假装表格该有图；
       真正渲染图片的是通知栏复习视图，那段在 verify_a11y 里验。 */
    const closeBtn2 = Array.from(doc.querySelectorAll('.modal button'))
      .find((b) => (b.getAttribute('aria-label') || '') === '关闭');
    if (closeBtn2) { closeBtn2.click(); }
    await wait(1500);

    check('页面里没有 src="undefined"（后端字段名写错就会这样）',
      !doc.body.innerHTML.includes('src="undefined"'),
      (doc.body.innerHTML.match(/src="undefined"/g) || []).length);

    /* 切到「题目管理」再断言：首页的列表受 currentFolderId / filterType
       过滤，刚建的题不一定在其中 —— 在这里断言列表内容会变成 flaky。 */
    const navQ = Array.from(doc.querySelectorAll('.nav__item'))
      .find((n) => n.textContent.includes('题目管理'));
    if (navQ) { navQ.click(); }
    await wait(1800);
    const rows = Array.from(doc.querySelectorAll('table tbody tr'));
    check('保存后题目出现在题目列表里',
      rows.some((tr) => tr.textContent.includes('带图题干')),
      rows.length + ' 行');
  }

  /* ---- 4. 重新打开该题，缩略图应还在 ---- */
  if (created) {
    const editBtn = Array.from(doc.querySelectorAll('button'))
      .find((b) => (b.getAttribute('aria-label') || '').includes('编辑'));
    if (editBtn) {
      editBtn.click();
      await wait(2000);
      const reopened = doc.querySelectorAll('.img-list img');
      check('重新打开题目时缩略图仍在', reopened.length >= 1, reopened.length);
      const closeBtn = Array.from(doc.querySelectorAll('.modal button'))
        .find((b) => (b.getAttribute('aria-label') || '') === '关闭');
      if (closeBtn) { closeBtn.click(); }
    }
  }

  const pre = document.createElement('pre');
  pre.id = 'img-results';
  pre.textContent = JSON.stringify(RESULTS);
  document.body.appendChild(pre);
})().catch((e) => {
  RESULTS.push({ name: '第三阶段运行无异常', ok: false,
                 detail: (e && e.message ? e.message : String(e)) });
  const pre = document.createElement('pre');
  pre.id = 'img-results';
  pre.textContent = JSON.stringify(RESULTS);
  document.body.appendChild(pre);
});
</script>
"""


def run_image_phase(target: str) -> list[dict]:
    """第三阶段：题干/答案插图（回归用）。"""
    out_dir = Path.home() / "AppData/Local/Temp/cuotiben_uiharness"
    out_dir.mkdir(parents=True, exist_ok=True)
    harness = out_dir / "img_harness.html"
    harness.write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>img-harness</title></head><body>"
        + IMAGE_PHASE.replace("window.__TARGET__", json.dumps(target))
        + "</body></html>", encoding="utf-8")
    url = target.rstrip("/") + "/__harness3"
    try:
        proc = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--force-device-scale-factor=1", "--window-size=1500,1150",
             "--virtual-time-budget=60000", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
        dom = proc.stdout or ""
        m = re.search(r'<pre id="img-results">(.*?)</pre>', dom, re.S)
        if not m:
            return [{"name": "第三阶段（插图）取到结果", "ok": False,
                     "detail": "未取到 img-results；DOM 尾部：" + dom[-400:]}]
        raw = (m.group(1).replace("&quot;", '"').replace("&amp;", "&")
               .replace("&lt;", "<").replace("&gt;", ">"))
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return [{"name": "第三阶段（插图）运行", "ok": False,
                 "detail": f"{type(exc).__name__}: {exc}"}]


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8941/"
    db_arg = sys.argv[2] if len(sys.argv) > 2 else None

    print("=" * 78)
    print("app.js 浏览器验证")
    print("=" * 78)

    try:
        counts = seed(target)
        print("种子数据:", json.dumps(counts, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(f"种子数据失败（服务是否已启动？）：{exc}")
        return 2

    # 复习队列默认是空的（新建题的首次复习在 3 天后）。
    # 本步要验证通知面板与复习页，所以把到期时间回拨，
    # 必须在浏览器跑之前做，否则队列是空的。
    if db_arg:
        try:
            n = force_due(Path(db_arg))
            print(f"已把 {n} 条复习记录回拨为逾期/今日到期")
        except Exception as exc:  # noqa: BLE001
            print(f"回拨复习时间失败：{exc}")
            return 2
    else:
        print("未提供数据库路径，跳过回拨（复习相关断言可能因队列为空而失败）")
    print()
    out_dir = Path.home() / "AppData/Local/Temp/cuotiben_uiharness"
    out_dir.mkdir(parents=True, exist_ok=True)
    harness = out_dir / "harness.html"
    harness.write_text(build_harness(target), encoding="utf-8")

    # 必须从**同源**加载测试页，否则 file:// 与 http:// 之间无法访问 iframe DOM
    harness_url = target.rstrip("/") + "/__harness"
    print(f"测试页: {harness_url}")
    print()

    proc = subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--force-device-scale-factor=1", "--window-size=1500,1050",
         "--virtual-time-budget=90000", "--dump-dom", harness_url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    dom = proc.stdout or ""
    match = re.search(r'<pre id="test-results">(.*?)</pre>', dom, re.S)
    if not match:
        print("未取到测试结果。DOM 片段：")
        print(dom[:2000])
        return 2

    raw = match.group(1)
    raw = (raw.replace("&quot;", '"').replace("&amp;", "&")
              .replace("&lt;", "<").replace("&gt;", ">"))
    results = json.loads(raw)

    # ---------- 第二阶段：大类创建入口（回归）----------
    # 这段单独跑，因为要先把大类删掉、造出"学科下没有大类"的状态，
    # 而第一阶段依赖已有大类来录题。放在同一个 iframe 里会把它的
    # 前置数据破坏掉。
    results += run_category_entry_phase(target)
    results += run_image_phase(target)

    passed = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"[{mark}] {r['name']}" + (f" — {r['detail']}" if r["detail"] else ""))
    print("-" * 78)
    print(f"合计 {len(results)} 项，通过 {len(passed)}，失败 {len(failed)}")
    for r in failed:
        print(f"  FAILED: {r['name']} — {r['detail']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
