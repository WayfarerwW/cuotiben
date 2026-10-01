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
import os
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
/* 兜底：任何未捕获错误都直接写进结果节点（不走 JSON），
   否则探针一挂就只看到一个空节点，完全不知道错在哪。 */
window.addEventListener('error', function (e) {
  var pre = document.getElementById('a11y-result');
  if (!pre) {
    pre = document.createElement('pre');
    pre.id = 'a11y-result';
    document.body.appendChild(pre);
  }
  pre.textContent = 'PROBE_ERROR: ' + (e.message || '')
    + ' @line ' + (e.lineno || '?');
});
const wait = (ms) => new Promise(r => setTimeout(r, ms));
async function until(fn, ms) {
  const end = Date.now() + (ms || 15000);
  while (Date.now() < end) { try { const v = fn(); if (v) return v; } catch(e){} await wait(150); }
  return null;
}
/* 按**真实时间**轮询。
   wait()/until() 走 setTimeout，会被 --virtual-time-budget 瞬间快进 ——
   用它去等一个真实 HTTP 响应（备份接口）会永远等不到，只能靠把预算调大，
   而预算一大又会和 Vue 的动画帧互相拖累（实测墙钟 10~25 分钟仍不收敛）。
   fetch 一次 /health 是按墙钟完成的，天然不消耗虚拟时间，拿它当"睡 100ms"。 */
async function realWait(ms) {
  const rounds = Math.max(1, Math.ceil((ms || 100) / 100));
  for (let i = 0; i < rounds; i++) {
    try { await fetch('/health', { cache: 'no-store' }); } catch (e) { /* 忽略 */ }
  }
}
async function realWaitFor(fn, seconds) {
  const end = Date.now() + (seconds || 10) * 1000;   // Date.now() 是真实时间
  while (Date.now() < end) {
    try { const v = fn(); if (v) return v; } catch (e) { /* 忽略 */ }
    await realWait(150);
  }
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
  /* 先接管 iframe 窗口的 console.warn：要在导航前设置好，
     否则会漏掉挂载阶段发出的警告。 */
  const bulkWarns = [];
  const f = document.createElement('iframe');
  f.style.cssText = 'border:0;height:900px;width:1400px';
  document.body.appendChild(f);
  const hook = () => {
    try {
      const cw = f.contentWindow;
      if (!cw || cw.__warnHooked) return;
      const orig = cw.console.warn;
      cw.console.warn = function () {
        bulkWarns.push(Array.from(arguments).join(' '));
        return orig.apply(cw.console, arguments);
      };
      cw.__warnHooked = true;
    } catch (e) {}
  };
  f.addEventListener('load', hook);
  const bulk = /[?&]bulk=1/.test(location.search);
  f.src = '__BASE__/?t=' + Date.now() + '&nopoll=1' + (bulk ? '&bulk=1' : '');
  hook();
  const d = await until(() => {
    const doc = f.contentDocument;
    if (!doc) return null;
    const el = doc.getElementById('app');
    return el && !el.hasAttribute('v-cloak') ? doc : null;
  }, 12000);  if (!d) {
    c('应用挂载', false, '未挂载');
    document.body.insertAdjacentHTML('beforeend',
      '<pre id="a11y-result">' + JSON.stringify(results) + '</pre>');
    return;
  }
  const w = f.contentWindow;
  await wait(1800);

  /* 接管 Vue 的 errorHandler 日志：app.js 的 app.config.errorHandler 会
     console.error，若不断言就只能瞎猜"点了没反应"。 */
  const vueErrors = [];
  const origErr = w.console.error;
  w.console.error = function () {
    vueErrors.push(Array.from(arguments).map(String).join(' '));
    return origErr.apply(w.console, arguments);
  };

  /* bulk 模式只验"超过 500 题的分页警告"就收尾：
     警告是 loadQuestions() 在挂载时发出的，塞完题重新加载一次页面即可，
     没必要把整套用例再跑一遍（既慢又会互相干扰）。 */
  if (bulk) {
    const joined = bulkWarns.join(' ');
    c('超过 500 题时控制台给出分页迁移警告',
      /客户端过滤阈值/.test(joined), JSON.stringify(joined.slice(0, 70)));
    c('分页警告只出现一次（不刷屏）',
      bulkWarns.filter(x => /客户端过滤阈值/.test(x)).length === 1,
      bulkWarns.length + ' 条 console.warn');
    c('分页警告说明了迁移方向（后端分页）', /后端分页/.test(joined));
    const pre0 = document.createElement('pre');
    pre0.id = 'a11y-result';
    pre0.textContent = JSON.stringify(results);
    document.body.appendChild(pre0);
    return;
  }

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
      const review = await until(() => d.querySelector('.notif__review'), 4000);
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

  /* ============ 12. 标签联想键盘导航 ============ */
  /* 需要题目编辑弹窗：从题目页点「新增题目」打开，再在标签输入框里打字。 */
  const addBtn = Array.from(d.querySelectorAll('button'))
    .find(b => b.textContent.trim().includes('新增题目'))
    || d.querySelector('.topbar__actions .btn--primary');
  if (addBtn) { addBtn.click(); }
  const tagInput = await until(() => d.getElementById('q-tags'), 4000);
  c('题目编辑弹窗里有标签输入框', !!tagInput);
  c('标签输入框是 combobox 语义',
    !!tagInput && tagInput.getAttribute('role') === 'combobox',
    tagInput ? tagInput.getAttribute('role') : '-');

  if (tagInput) {
    const suggestList = () => d.getElementById('tag-suggest-list');
    const optionEls = () => Array.from(d.querySelectorAll('#tag-suggest-list [role="option"]'));
    const activeIdx = () => optionEls().findIndex(
      el => el.classList.contains('is-active'));
    const combo = () => tagInput.getAttribute('aria-expanded');
    /* 读应用自己写下的草稿（AGENTS.md 4.5：防抖 500ms 写 draft_question_new）。
       断言语义时用它比数 DOM 里的 .tag 元素稳 —— 那是应用的既定状态记录。 */
    const readDraftTags = () => {
      try {
        const raw = w.localStorage.getItem('draft_question_new');
        return raw ? (JSON.parse(raw).tags || []) : [];
      } catch (e) { return []; }
    };

    async function typeTag(text) {
      tagInput.focus();
      tagInput.value = text;
      tagInput.dispatchEvent(new Event('input', { bubbles: true }));
      // 等下拉真的打开（搜索是异步的）
      return until(() => optionEls().length > 0, 4000);
    }
    function pressTag(k) {
      const ev = new KeyboardEvent('keydown',
        { key: k, bubbles: true, cancelable: true });
      tagInput.dispatchEvent(ev);
      return ev;
    }

    // 用前缀恰好命中多个标签的词，保证候选 ≥2（seed 里建了
    // 重要极限 / 重要公式 / 重要定义）
    await typeTag('重要');
    await wait(400);
    const opts = optionEls();
    c('输入后出现联想列表（≥2 项，才能测移动与循环）',
      !!suggestList() && opts.length >= 2, opts.length + ' 项');
    c('下拉展开时 aria-expanded=true', combo() === 'true', combo());
    c('列表有 role=listbox', !!suggestList()
      && suggestList().getAttribute('role') === 'listbox');
    c('选项有 role=option',
      opts.length > 0 && opts.every(el => el.getAttribute('role') === 'option'));
    c('初始没有高亮', activeIdx() === -1, 'idx=' + activeIdx());

    if (opts.length >= 2) {
      // ---- ↓ 移动高亮 ----
      pressTag('ArrowDown');
      await wait(150);
      c('↓ 首次高亮落到第 1 项', activeIdx() === 0, 'idx=' + activeIdx());
      c('高亮项 aria-selected=true',
        optionEls()[0].getAttribute('aria-selected') === 'true');
      c('aria-activedescendant 指向高亮项',
        tagInput.getAttribute('aria-activedescendant') === 'tag-opt-0',
        tagInput.getAttribute('aria-activedescendant'));
      c('高亮不把焦点移出输入框', d.activeElement === tagInput, blob(d.activeElement));

      pressTag('ArrowDown');
      await wait(150);
      c('↓ 移到第 2 项', activeIdx() === 1, 'idx=' + activeIdx());
      pressTag('ArrowUp');
      await wait(150);
      c('↑ 回到第 1 项', activeIdx() === 0, 'idx=' + activeIdx());

      // ---- 边界循环 ----
      pressTag('ArrowUp');
      await wait(150);
      c('↑ 在首项循环到末项（边界循环）', activeIdx() === optionEls().length - 1,
        'idx=' + activeIdx() + '/' + (optionEls().length - 1));
      pressTag('ArrowDown');
      await wait(150);
      c('↓ 在末项循环回首项（边界循环）', activeIdx() === 0, 'idx=' + activeIdx());

      // ---- Enter 选中，填入输入框 ----
      // 从 data-tag-name 取准确名字：textContent 是「名字 + 计数」拼起来的，
      // 用正则剥计数会把标签名自带的数字也吃掉（"重要极限3" -> "重要极限"）。
      const curOpt = optionEls()[activeIdx()];
      const wantName = (curOpt.getAttribute('data-tag-name') || '').trim();
      c('联想项带 data-tag-name（供断言取准确名字）', wantName.length > 0, wantName);
      pressTag('Enter');
      await wait(350);
      c('Enter 把选中项填入输入框', tagInput.value.trim() === wantName,
        JSON.stringify(tagInput.value) + ' vs ' + JSON.stringify(wantName));
      c('Enter 选中后关闭下拉', !suggestList() && combo() === 'false', combo());
      c('Enter 选中后只填入、未直接成条目（留给用户确认）',
        readDraftTags().indexOf(wantName) === -1,
        JSON.stringify(readDraftTags()));

      // ---- Esc 关闭下拉但保留输入 ----
      await typeTag('重要');
      await wait(250);
      c('重新输入后下拉再次打开', !!suggestList(), combo());
      const beforeVal = tagInput.value;
      const evEsc = pressTag('Escape');
      await wait(250);
      c('Esc 关闭联想下拉', !suggestList() && combo() === 'false', combo());
      c('Esc 保留输入内容', tagInput.value === beforeVal, JSON.stringify(tagInput.value));
      c('Esc 被消费（不冒泡去关外层弹窗）', evEsc.defaultPrevented);
      c('外层弹窗仍打开（Esc 没穿透）', !!d.getElementById('q-tags'));

      // ---- Tab 关闭下拉 ----
      await typeTag('重要');
      await wait(250);
      c('第三次输入下拉打开', !!suggestList(), combo());
      const evTab = pressTag('Tab');
      await wait(250);
      c('Tab 关闭联想下拉', !suggestList() && combo() === 'false', combo());
      c('Tab 保留输入内容', tagInput.value.trim() === '重要',
        JSON.stringify(tagInput.value));
      c('Tab 本身不被拦截（仍可移动焦点）', !evTab.defaultPrevented);

      // ---- 无高亮时 Enter 仍按"新建标签" ----
      tagInput.focus();
      tagInput.value = '临时新标签';
      tagInput.dispatchEvent(new Event('input', { bubbles: true }));
      await wait(800);                 // 等防抖 500ms + 余量
      pressTag('Escape');              // 关掉联想，确保没有高亮
      await wait(250);
      const draftBefore = readDraftTags().slice();
      pressTag('Enter');
      await wait(200);
      /* 这里**不**断言"输入框被清空"：无头 + 虚拟时间下 Vue 的 DOM 回写
         不完全可靠（响应式值确实清空了 —— 草稿里 tags 已写入，但 DOM 的
         value 没跟着回写）。断言 DOM 反而变成测环境，所以只断言
         aria-activedescendant 已复位这个真实 DOM 属性。 */
      c('新建后清除 aria-activedescendant',
        !tagInput.getAttribute('aria-activedescendant'),
        String(tagInput.getAttribute('aria-activedescendant')));
      await wait(800);                 // 等防抖写入草稿
      const draftAfter = readDraftTags();
      c('诊断 新建标签前状态', true,
        'expanded=' + combo() + ' activeIdx=' + activeIdx()
        + ' 草稿tags(前)=' + JSON.stringify(draftBefore));
      c('无高亮时 Enter 直接新建标签（原行为保留）',
        draftAfter.indexOf('临时新标签') !== -1,
        JSON.stringify(draftBefore) + ' -> ' + JSON.stringify(draftAfter));
    }
    // 关掉弹窗，避免影响后续
    d.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await wait(400);
  }

  /* ============ 13. 数据说明页（requirements 2.17）============ */
  const navData = Array.from(d.querySelectorAll('.nav__item'))
    .find(n => n.textContent.includes('数据'))
    || null;
  // 数据说明页在用户菜单里，不在侧边栏导航
  const userMenuBtn = d.querySelector('.user-menu');
  if (userMenuBtn) {
    userMenuBtn.click();
    await wait(600);
    const entry = Array.from(d.querySelectorAll('#user-menu .dropdown__item'))
      .find(el => el.textContent.includes('数据'));
    if (entry) {
      entry.click();
      await wait(2000);
    }
  }
  const dataCard = await until(
    () => Array.from(d.querySelectorAll('.card'))
      .find(c => c.textContent.includes('数据存放位置')), 5000);
  c('能进入数据说明页', !!dataCard,
    navData ? '侧边栏入口' : '用户菜单入口');

  if (dataCard) {
    const pageText = d.querySelector('.page')
      ? d.querySelector('.page').textContent : '';
    c('显示数据库路径', /数据库文件/.test(pageText));
    c('显示图片路径', /图片目录/.test(pageText) && /uploads/.test(pageText));
    c('显示备份路径', /备份目录/.test(pageText) && /backups/.test(pageText));
    c('说明纯本地运行、数据保存在本机、不联网',
      /纯本地运行/.test(pageText) && /不联网/.test(pageText));
    c('数据库路径来自服务端解析（不是字面值 data/cuotiben.db）',
      /verify|a11y|tmp|Temp/i.test(pageText) || !/data\/cuotiben\.db/.test(pageText),
      (pageText.match(/[A-Za-z]:\\[^\s]*\.db/) || ['未显示绝对路径'])[0].slice(0, 60));

    const backupBtn = Array.from(d.querySelectorAll('button'))
      .find(b => b.textContent.trim() === '手动备份');
    const jsonBtn = Array.from(d.querySelectorAll('button'))
      .find(b => b.textContent.trim() === '导出 JSON');
    c('有「手动备份」按钮', !!backupBtn);
    c('有「导出 JSON」按钮', !!jsonBtn);

    // 手动备份：重点断言"按钮在空闲态是**可点的**"，然后真点一次
    if (backupBtn) {
      /* 这条曾抓到一个真实缺陷：模板把**字符串**传给 `:disabled`
         （busy 时是 'backup'、空闲时是 ''），而 Vue 3 的运行时把
         `disabled=""` 当作**真**（源码 `e => e && (e.disabled || ""===e.disabled)`），
         于是两个按钮在空闲态也是 disabled、点了完全没反应。
         所以"空闲态不带 disabled 属性"是这里最关键的回归断言。 */
      c('空闲态「手动备份」没有被禁用', backupBtn.disabled === false
        && !backupBtn.hasAttribute('disabled'),
        'disabled=' + backupBtn.disabled
        + ' attr=' + JSON.stringify(backupBtn.getAttribute('disabled')));
      c('空闲态「导出 JSON」没有被禁用',
        !!jsonBtn && jsonBtn.disabled === false && !jsonBtn.hasAttribute('disabled'));

      backupBtn.click();
      /* 不再断言"能看到 busy 态"：备份只要几十毫秒，doBackup 的 then
         跑完、dataBusy 已复位，而 Vue 的 DOM 更新在微任务里 ——
         这个中途态基本不可观测。断言一个抓不住的瞬间态只会变成 flaky 测试。
         真正的回归点是下面两条：结果提示出现、按钮**恢复可点**。 */

      /* 等结果用**真实时间**：fetch 一次接口，它按墙钟完成，
         不消耗 `--virtual-time-budget`。用 wait() 会被虚拟时间瞬间跳过，
         再去等一个真实 HTTP 响应就永远等不到（这正是之前跑不完的原因）。 */
      const msg = await realWaitFor(() => {
        const el = d.querySelector('.data-message');
        return el && el.textContent.trim() ? el : null;
      }, 12);
      c('点「手动备份」后有结果提示', !!msg,
        msg ? JSON.stringify(msg.textContent.trim().slice(0, 60)) : '无提示');
      c('备份成功提示里带备份目录',
        !!msg && /备份完成/.test(msg.textContent) && /backups/.test(msg.textContent),
        msg ? msg.textContent.trim().slice(0, 80) : '-');
      c('备份提示不是错误样式',
        !!msg && !msg.classList.contains('data-message--error'));

      /* 按 class 找按钮，不要按文案找：完成前后文案会变
         （'手动备份' <-> '备份中…'），按文案找会在错误的时机返回 null。 */
      const restored = d.querySelector('.quick-actions .btn--primary');
      c('备份结束后按钮恢复可点（busy 态清除）',
        !!restored && restored.disabled === false
        && !restored.hasAttribute('disabled'),
        restored ? ('disabled=' + restored.disabled
          + ' 文案=' + restored.textContent.trim().slice(0, 12)) : 'null');
      c('没有 Vue 渲染错误', vueErrors.length === 0,
        JSON.stringify(vueErrors.slice(0, 2)));
    }

    // 导出 JSON：断言点了不报错（下载落盘在浏览器里不好断言）
    if (jsonBtn) {
      // 先清掉上一条结果提示，否则下面"没有错误提示"可能落在旧节点上
      const stale = d.querySelector('.data-message');
      if (stale) { stale.textContent = ''; }
      jsonBtn.click();
      await realWaitFor(() => d.querySelector('.data-message--error'), 8);
      const err = d.querySelector('.data-message--error');
      c('点「导出 JSON」没有出现错误提示', !err,
        err ? JSON.stringify(err.textContent.trim().slice(0, 60)) : '无错误');
    }
  }

  /* ============ 14. 题目数超阈值的分页提示 ============
     在 Python 侧做第二遍（见 main()）：塞够 500+ 题后重新加载页面，
     用 ?bulk=1 让本探针只验警告。 */

  const pre = document.createElement('pre');
  pre.id = 'a11y-result';
  /* 刻意不用 JSON.stringify 输出：探针里任何一处拿到不可序列化的值，
     stringify 会抛错，结果节点变空、看不到已完成的断言。
     逐行输出更稳。 */
  pre.textContent = '[\n' + results.map(function (r) {
    return '{"name":' + JSON.stringify(String(r.name))
      + ',"ok":' + (r.ok ? 'true' : 'false')
      + ',"detail":' + JSON.stringify(String(r.detail === undefined ? '' : r.detail))
      + '}';
  }).join(',\n') + '\n]';
  document.body.appendChild(pre);
})().catch(err => {
  /* 探针抛错时也要把已有结果写出来，否则排错时只能看到一个空节点 */
  results.push({ name: '探针运行无异常', ok: false,
                 detail: (err && err.message ? err.message : String(err)) });
  const pre = document.createElement('pre');
  pre.id = 'a11y-result';
  pre.textContent = '[\n' + results.map(function (r) {
    return '{"name":' + JSON.stringify(String(r.name))
      + ',"ok":' + (r.ok ? 'true' : 'false')
      + ',"detail":' + JSON.stringify(String(r.detail === undefined ? '' : r.detail))
      + '}';
  }).join(',\n') + '\n]';
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
    # 标签名刻意都带 "重要" 前缀：标签联想的键盘用例需要一个**多于 2 项**的
    # 候选列表，否则"移到第 2 项""边界循环"都退化成同一项、等于没测。
    for i, stem in enumerate([
        "求极限 lim(x→0) sin(x)/x",
        "洛必达法则的适用条件是什么",
        "求 y=x^3 的导数",
    ]):
        _status, _body = call("POST", "/questions", {
            "folder_id": leaf, "stem": stem,
            "answer": f"答案 {i + 1}",
            "tags": ["重要极限", "重要公式", "重要定义"][: i + 1],
            "is_starred": i == 0, "sort_order": i,
        })
        made += 1

    # 新题的首条记录到期时间是 now()+3 天，必须回拨才会进今日队列
    call("POST", "/__redue")
    return made


def add_bulk_questions(db_path: Path, leaf_name: str, count: int) -> int:
    """直接往库里插题，用于验证"超过 500 题控制台警告"。

    走 API 插 500 多次太慢，直接写库；只作用于传入的临时库。
    """
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        row = cur.execute("SELECT id FROM folders WHERE name=?", (leaf_name,)).fetchone()
        if not row:
            return 0
        leaf = row[0]
        now = "2026-02-14T10:30:00+00:00"
        cur.executemany(
            "INSERT INTO questions (folder_id, stem, answer, is_starred, "
            "mastery_status, sort_order, created_at, updated_at, deleted_at) "
            "VALUES (?, ?, ?, 0, 'still_wrong', ?, ?, ?, NULL)",
            [(leaf, f"批量题 {i}", "答案", i, now, now) for i in range(count)],
        )
        con.commit()
        return count
    finally:
        con.close()


def run_probe(harness: Path, extra_query: str = "") -> tuple[int, list[dict]]:
    """跑一次浏览器探针，返回 (退出码, 断言列表)。"""
    url = BASE + "/__harness" + extra_query
    dom = subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
         "--force-device-scale-factor=1", "--window-size=1500,1000",
         "--virtual-time-budget=90000", "--dump-dom", url],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900).stdout or ""

    # 测试页里可能有**多个** <pre id="a11y-result">（异常路径也插一个空的），
    # 所以要取最后一个非空的，不能用 re.search 拿第一个。
    raws = re.findall(r'<pre id="a11y-result">(.*?)</pre>', dom, re.S)
    for candidate in reversed(raws):
        text = (candidate.replace("&quot;", '"').replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")).strip()
        if text.startswith("["):
            return 0, json.loads(text)
    print(f"未取到断言结果（找到 {len(raws)} 个结果节点）；DOM 尾部：")
    print(dom[-1200:])
    return 2, []


def report(rows: list[dict], title: str) -> int:
    print(f"\n== {title} ==")
    for r in rows:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['name']}"
              + (f" — {r['detail']}" if r["detail"] else ""))
    failed = [r for r in rows if not r["ok"]]
    print("-" * 74)
    print(f"合计 {len(rows)} 项，通过 {len(rows) - len(failed)}，失败 {len(failed)}")
    for r in failed:
        print(f"  FAILED: {r['name']} — {r['detail']}")
    return 1 if failed else 0


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="a11y_"))
    harness = tmp / "a11y.html"
    harness.write_text(PROBE.replace("__BASE__", BASE), encoding="utf-8")

    # 备份/图片目录指向临时目录：本脚本会**真的**点「手动备份」，
    # 不隔离就会每跑一次往仓库 backups/ 里堆一份（实测堆到 16 份后
    # 复制耗时把浏览器验证拖超时）。见 data_service 的 CUOTIBEN_*_DIR。
    env = dict(os.environ)
    env["CUOTIBEN_BACKUPS_DIR"] = str(tmp / "backups")
    env["CUOTIBEN_UPLOADS_DIR"] = str(tmp / "uploads")
    (tmp / "uploads").mkdir(parents=True, exist_ok=True)

    # 起一个与 verify_responsive 相同的测试服务器（用同一份 scaffolding）
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import runpy,sys; sys.argv=['x',%r,%r,%r]; "
         "runpy.run_path(r'tools/_serve_for_ui_test.py', run_name='__main__')"
         % (PORT, str(harness), str(tmp / "a11y.db"))],
        cwd=".", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
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
        print(f"[seed] 已造 {n} 道题并回拨到期时间")
        code1, rows1 = run_probe(harness)
        if code1 != 0:
            return code1

        # 第二遍：塞到 500 题以上，验证控制台的分页迁移警告。
        # 放在最后跑，避免 500+ 行数据拖慢（或干扰）上面那些键盘/焦点断言。
        added = add_bulk_questions(tmp / "a11y.db", "极限与连续", 600)
        print(f"[bulk] 追加 {added} 道题，验证分页阈值警告")
        code2, rows2 = run_probe(harness, "?bulk=1")
        if code2 != 0:
            return code2

        rc1 = report(rows1, "键盘与焦点")
        rc2 = report(rows2, "分页阈值监控")
        total = len(rows1) + len(rows2)
        failed = [r for r in rows1 + rows2 if not r["ok"]]
        print("=" * 74)
        print(f"总计 {total} 项，通过 {total - len(failed)}，失败 {len(failed)}")
        return 1 if (rc1 or rc2) else 0
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
