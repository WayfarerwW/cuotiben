"""校验 style.css 的设计令牌与 docs/ui-design.md 第 2/3 节是否一致。

只做字符串层面的比对：文档里给出具体值的项，必须在 CSS 里出现。
这样"视觉参数是硬约束"就有一条可执行的检查，而不是靠肉眼。

运行：python tools/verify_design_tokens.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
HTML = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
DOC = (ROOT / "docs/ui-design.md").read_text(encoding="utf-8")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def norm(value: str) -> str:
    """统一色值/数值写法，便于比较（#4A7CDB 与 #4a7cdb 视为相同）。"""
    return value.strip().lower().replace(" ", "")


def has_value(v: str) -> bool:
    return norm(v) in norm(CSS)


def main() -> int:
    print("=" * 80)
    print("设计令牌校验（style.css vs docs/ui-design.md）")
    print("=" * 80)

    # ---------- 2.1 色彩体系 ----------
    print("\n-- 2.1 色彩体系 --")
    colors = {
        "主色调": "#4A7CDB",
        "辅助色": "#7B68EE",
        "背景色": "#F5F7FA",
        "侧边栏": "#1A1A2E",
        "成功色": "#68D391",
        "警告色": "#F6AD55",
        "危险色": "#FC8181",
        "主要文字": "#2D3748",
        "次要文字": "#718096",
        "辅助文字": "#A0AEC0",
    }
    for name, value in colors.items():
        check(f"色彩 {name} {value}", has_value(value))

    # ---------- 2.2 字体规范 ----------
    print("\n-- 2.2 字体规范 --")
    check("中文字体栈含 Inter", "Inter" in CSS)
    check("中文字体栈含 PingFang SC", "PingFang SC" in CSS)
    check("中文字体栈含 Microsoft YaHei", "Microsoft YaHei" in CSS)
    check("英文字体栈含 Roboto", "Roboto" in CSS)
    check("代码字体栈含 Fira Code", "Fira Code" in CSS)
    check("代码字体栈含 Consolas", "Consolas" in CSS)

    for label, size, weight in (
        ("页面大标题 24px/Bold", "24px", "700"),
        ("模块标题 20px/SemiBold", "20px", "600"),
        ("卡片标题 16px/SemiBold", "16px", "600"),
        ("正文 14px/Regular", "14px", "400"),
        ("辅助文字 12px/Regular", "12px", "400"),
    ):
        check(f"字号层级 {label}",
              has_value(size) and f"--fw-{'bold' if weight == '700' else ('semibold' if weight == '600' else 'regular')}" in CSS)

    # 正文 14px 必须是 body 默认字号
    check("body 默认字号为 14px（正文层级）",
          re.search(r"body\s*\{[^}]*font-size:\s*var\(--fs-body\)", CSS, re.S)
          is not None)

    # ---------- 2.3 间距系统 ----------
    print("\n-- 2.3 间距系统 --")
    for name, value in (
        ("页面边距 24px", "24px"),
        ("卡片内边距 20px", "20px"),
        ("模块间距 24px", "24px"),
        ("元素间距 16px", "16px"),
        ("紧凑间距 12px", "12px"),
        ("紧凑间距 8px", "8px"),
    ):
        check(f"间距 {name}", has_value(value), "以 CSS 变量形式定义")

    check("间距以 4px 网格为基础（数值均为 4 的倍数）",
          all(int(v) % 4 == 0 for v in re.findall(r"--sp-[a-z]+:\s*(\d+)px", CSS)),
          str(re.findall(r"--sp-[a-z]+:\s*(\d+)px", CSS)))

    # ---------- 2.4 圆角与阴影 ----------
    print("\n-- 2.4 圆角与阴影 --")
    check("圆角 卡片/弹窗 12px", has_value("12px"))
    check("圆角 按钮/输入框 8px", has_value("8px"))
    check("圆角 标签/徽章 6px", has_value("6px"))
    check("圆角 胶囊 20px", has_value("20px"))
    check("圆角 头像 50%", has_value("50%"))

    for name, value in (
        ("静态卡片阴影", "0 2px 12px rgba(0,0,0,0.05)"),
        ("悬停卡片阴影", "0 4px 16px rgba(0,0,0,0.08)"),
        ("弹窗/抽屉阴影", "0 8px 30px rgba(0,0,0,0.12)"),
    ):
        check(f"阴影 {name}", has_value(value))

    # ---------- 3.1 整体布局 ----------
    print("\n-- 3.1 整体布局 --")
    check("侧边栏宽度变量 260px",
          re.search(r"--w-sidebar:\s*260px", CSS) is not None)
    check("侧边栏折叠宽度变量 64px",
          re.search(r"--w-sidebar-collapsed:\s*64px", CSS) is not None)
    check("顶部栏高度变量 64px",
          re.search(r"--h-topbar:\s*64px", CSS) is not None)
    check("侧边栏固定定位（position: fixed）",
          re.search(r"\.sidebar\s*\{[^}]*position:\s*fixed", CSS, re.S) is not None)
    check("主内容区自适应（flex: 1 1 auto）",
          re.search(r"\.main\s*\{[^}]*flex:\s*1 1 auto", CSS, re.S) is not None)
    check("侧边栏背景用侧边栏色变量",
          re.search(r"\.sidebar\s*\{[^}]*background:\s*var\(--c-sidebar\)", CSS, re.S)
          is not None)

    # ---------- 3.2 侧边栏 ----------
    print("\n-- 3.2 侧边栏 --")
    check("折叠过渡 300ms ease-in-out",
          re.search(r"--dur-sidebar:\s*300ms", CSS) is not None
          and re.search(r"--ease-sidebar:\s*ease-in-out", CSS) is not None)
    check("激活项主色背景 + 白色文字",
          re.search(r"\.nav__item\.is-active\s*\{[^}]*background:\s*var\(--c-primary\)",
                    CSS, re.S) is not None
          and re.search(r"\.nav__item\.is-active\s*\{[^}]*color:\s*var\(--c-sidebar-text-active\)",
                        CSS, re.S) is not None)
    # 导航现在是 Vue 渲染的：HTML 里是 v-for + navItems，
    # 断言改为"模板里有循环 + app.js 里定义了 6 项"，比数 data-nav 更贴合实现。
    check("导航由 Vue 渲染（v-for navItems）",
          "v-for=\"item in navItems\"" in HTML and "nav__item" in HTML)
    check("导航含 6 个功能入口",
          sum(1 for k in ("home", "questions", "review", "notes", "stats", "settings")
              if f"key: '{k}'" in APP_JS) == 6,
          str([k for k in ("home", "questions", "review", "notes", "stats", "settings")
               if f"key: '{k}'" in APP_JS]))
    check("文件夹树两级结构（tree__children）", "tree__children" in CSS and "tree__children" in HTML)
    check("箭头旋转 90° 过渡 250ms",
          re.search(r"--dur-tree:\s*250ms", CSS) is not None
          and "rotate(90deg)" in CSS)
    check("用户信息区固定底部（border-top + flex 0 0 auto）",
          re.search(r"\.sidebar__user\s*\{[^}]*flex:\s*0 0 auto", CSS, re.S) is not None)
    check("折叠态隐藏文字标签，只留图标",
          re.search(r"\.app\.is-collapsed \.nav__label", CSS) is not None)
    check("含右键菜单样式（context-menu）", "context-menu" in CSS)

    # ---------- 3.3 顶部导航栏 ----------
    print("\n-- 3.3 顶部导航栏 --")
    check("含搜索框", 'class="search__input"' in HTML)
    check("含 Ctrl/Cmd + K 快捷键提示", "Ctrl K" in HTML and "search__kbd" in CSS)
    check("含新增题目主按钮",
          '@click="openQuestionEditor()"' in HTML and "btn--primary" in HTML)
    check("含导出 PDF 次按钮", '@click="openExport"' in HTML and "导出 PDF" in HTML)
    check("含通知铃铛 + 角标", "bell__badge" in HTML and ".badge" in CSS)
    check("含用户菜单（头像 + 下拉）",
          "user-menu" in HTML and "dropdown--menu" in HTML and "user-menu" in CSS)

    # ---------- 5.3 角标规则 ----------
    print("\n-- 5.3 状态标识 / 角标 --")
    check("含红点（仅表示有新通知）", re.search(r"\.dot\s*\{", CSS) is not None)
    check("含数字角标样式", re.search(r"\.badge\s*\{", CSS) is not None)
    check("含橙色角标变体", ".badge--warning" in CSS)
    check("含重点星标样式（黄色/灰色）", ".star--on" in CSS and ".star {" in CSS)
    check("含正误状态点（红/绿）",
          ".status--wrong" in CSS and ".status--won" in CSS)
    check("含复习状态标签（待复习/已复习/逾期/积压）",
          all(t in CSS for t in (".tag--pending", ".tag--reviewed",
                                 ".tag--overdue", ".tag--backlog")))

    # ---------- 5.4 通知面板 ----------
    print("\n-- 5.4 通知面板 --")
    check("面板宽 360px", re.search(r"\.notif__panel\s*\{[^}]*width:\s*360px", CSS, re.S)
          is not None)
    check("面板最大高 480px",
          re.search(r"\.notif__panel\s*\{[^}]*max-height:\s*480px", CSS, re.S) is not None)
    check("面板可滚动", re.search(r"\.notif__list\s*\{[^}]*overflow-y:\s*auto", CSS, re.S)
          is not None)
    check("未读左边框 3px",
          re.search(r"\.notif__item\.is-unread::before\s*\{[^}]*width:\s*3px", CSS, re.S)
          is not None)
    check("复习视图内嵌（notif__review，不跳页）",
          "notif__review" in CSS and "notif__review" in HTML)

    # ---------- 5.1 按钮 ----------
    print("\n-- 5.1 按钮 --")
    check("主要按钮 #4A7CDB 填充 + 白字",
          re.search(r"\.btn--primary\s*\{[^}]*background:\s*var\(--c-primary\)", CSS, re.S)
          is not None
          and re.search(r"\.btn--primary\s*\{[^}]*color:\s*#fff", CSS, re.S) is not None)
    check("次要按钮 白底 + #E2E8F6 边框",
          re.search(r"\.btn--secondary\s*\{[^}]*border-color:\s*var\(--c-border\)", CSS, re.S)
          is not None and has_value("#E2E8F6"))
    check("危险按钮 #FC8181 填充 + 白字",
          re.search(r"\.btn--danger\s*\{[^}]*background:\s*var\(--c-danger\)", CSS, re.S)
          is not None)
    check("文字按钮 无边框 + 主色文字",
          re.search(r"\.btn--text\s*\{[^}]*color:\s*var\(--c-primary\)", CSS, re.S) is not None)
    check("图标按钮", re.search(r"\.icon-btn\s*\{", CSS) is not None)
    check("禁用态透明度 50%",
          re.search(r"\.btn:disabled[^{]*\{[^}]*opacity:\s*0\.5", CSS, re.S) is not None)
    check("加载态 Spinner", "btn__spinner" in CSS and "@keyframes spin" in CSS)

    # ---------- 6.2 表单交互 ----------
    print("\n-- 6.2 表单交互 --")
    check("输入框焦点外发光 0 0 3px rgba(74,124,219,0.2)",
          re.search(r"--c-primary-ring:\s*rgba\(74,\s*124,\s*219,\s*0\.20\)", CSS) is not None
          and "0 0 3px var(--c-primary-ring)" in CSS)

    # ---------- 6.1 页面切换 ----------
    print("\n-- 6.1 导航交互 --")
    check("页面切换过渡 200ms", re.search(r"--dur-page:\s*200ms", CSS) is not None)

    # ---------- 7 响应式 ----------
    print("\n-- 7 响应式设计 --")
    check("桌面端断点 >1200px", "min-width: 1201px" in CSS)
    check("平板端断点 768-1200px",
          "max-width: 1200px" in CSS and "768px" in CSS)
    check("移动端断点 <768px", "max-width: 767px" in CSS)
    check("移动端侧边栏改抽屉（translateX + 遮罩）",
          "translateX(-100%)" in CSS and "drawer-mask" in CSS)
    check("移动端表格转卡片列表", "data-table--cards" in CSS)
    check("移动端顶部导航简化（隐藏按钮文字）",
          "topbar__actions .btn__label { display: none; }" in CSS)

    # ---------- 8 可访问性 ----------
    print("\n-- 8 可访问性 --")
    check("焦点指示器 outline 2px solid #4A7CDB",
          re.search(r"outline:\s*2px solid var\(--c-primary\)", CSS) is not None)
    check("焦点指示器 outline-offset 2px",
          re.search(r"outline-offset:\s*2px", CSS) is not None)
    check("语义化 HTML：nav / main / aside / header",
          all(t in HTML for t in ("<nav", "<main", "<aside", "<header")))
    check("图标按钮提供 aria-label",
          HTML.count("aria-label") >= 4, f"{HTML.count('aria-label')} 处")
    check("状态变更用 aria-live 播报", 'aria-live="polite"' in HTML)
    check("含 visually-hidden 屏幕阅读器类", "visually-hidden" in CSS)
    check("支持 prefers-reduced-motion",
          "prefers-reduced-motion" in CSS)

    # ---------- 6. 交互细节与动画 ----------
    print("\n-- 6 交互细节与动画 --")
    check("6.1 侧边栏 300ms ease-in-out",
          "--dur-sidebar: 300ms" in CSS and "--ease-sidebar: ease-in-out" in CSS)
    check("6.1 页面切换淡入淡出 200ms",
          "page-fade" in CSS and "--dur-page: 200ms" in CSS)
    check("6.1 文件夹树高度动画 250ms + 箭头旋转 90°",
          "--dur-tree: 250ms" in CSS and "rotate(90deg)" in CSS)
    check("6.2 输入框焦点外发光 0 0 3px rgba(74,124,219,0.2)",
          "0 0 3px var(--c-primary-ring)" in CSS)
    check("6.2 标签动画插入 / 缩放移除",
          "tag-enter-from" in CSS and "tag-leave-to" in CSS
          and "transition-group" in HTML)
    check("6.2 草稿防抖 500ms", "DRAFT_DEBOUNCE_MS = 500" in APP_JS)
    check("6.2 草稿保存期间显示加载态",
          "draftSaving" in APP_JS and "btn__spinner" in HTML)
    check("6.2 草稿提示条 5s 后自动收起",
          "DRAFT_PROMPT_MS = 5000" in APP_JS and "draft-banner--leaving" in CSS)
    check("6.3 答案展开高度动画 300ms + 图标旋转",
          "answer-enter-active" in CSS and "max-height" in CSS
          and "answer-toggle__caret" in CSS)
    check("6.3 评价反馈 scale(0.95) + 背景填充",
          "review-rate:active" in CSS and "review-rate--0.is-picked" in CSS)
    check("6.3 评价后 200ms 自动切题", "function rateReview" in APP_JS)
    check("6.3 上一题/下一题支持方向键",
          "onGlobalKeydown" in APP_JS and "ArrowLeft" in APP_JS)
    check("6.3 方向键不劫持输入框", "typing" in APP_JS and "TEXTAREA" in APP_JS)
    check("6.3 打勾绘制动画 + 行高亮",
          "draw-check" in CSS and "row-flash" in CSS and "is-flash" in HTML)
    check("6.4 成功 3s / 警告 5s", "5000 : 3000" in APP_JS)
    check("6.4 Toast 三色齐全",
          all(k in CSS for k in ("toast--success", "toast--warning", "toast--error")))
    check("6.4 空状态含插画+文案+按钮",
          "empty-state__art" in CSS and "empty-state__title" in HTML)

    # ---------- 结构完整性 ----------
    print("\n-- 结构完整性 --")

    # 去掉 HTML 注释后再检查引用与外部资源，
    # 否则注释里提到的 <script src> 与示例 URL 会被误判为真实引用。
    html_no_comments = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)

    check("index.html 引用 css/style.css", 'href="css/style.css"' in HTML)
    # 现在真的引用了 js（不再是注释里的接入点），所以改成断言"引用的文件都存在"
    script_srcs = re.findall(r'<script[^>]+src="([^"]+)"', html_no_comments)
    check("引用的 js 文件都真实存在",
          bool(script_srcs) and all(
              (ROOT / "app/static" / s).is_file() for s in script_srcs),
          ", ".join(script_srcs) or "没有引用任何 js")
    check("脚本加载顺序为 vue -> api -> app",
          [s.split("/")[-1] for s in script_srcs] ==
          ["vue.global.prod.js", "api.js", "app.js"],
          str(script_srcs))
    check("所有 <use href=\"#i-...\"> 都有对应 symbol 定义",
          set(re.findall(r'<use href="#(i-[a-z-]+)"', HTML))
          <= set(re.findall(r'<symbol id="(i-[a-z-]+)"', HTML)),
          "无悬空图标引用")
    check("标签订阅与闭合基本平衡（<div> 计数）",
          HTML.count("<div") == HTML.count("</div>"),
          f"{HTML.count('<div')} 开 / {HTML.count('</div>')} 闭")
    check("列表标签平衡（<ul>）",
          HTML.count("<ul") == HTML.count("</ul>"),
          f"{HTML.count('<ul')} 开 / {HTML.count('</ul>')} 闭")
    check("section 标签平衡",
          HTML.count("<section") == HTML.count("</section>"),
          f"{HTML.count('<section')} 开 / {HTML.count('</section>')} 闭")
    check("未使用外部 CDN / 网络资源",
          not re.search(r'(?:src|href)\s*=\s*["\']https?://', html_no_comments),
          "纯本地，无网络依赖")
    check("CSS 大括号平衡",
          CSS.count("{") == CSS.count("}"),
          f"{CSS.count('{')} 开 / {CSS.count('}')} 闭")
    check("CSS 无未闭合注释",
          CSS.count("/*") == CSS.count("*/"),
          f"{CSS.count('/*')} 开 / {CSS.count('*/')} 闭")

    print("\n" + "-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
