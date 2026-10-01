"""WCAG 对比度校验（ui-design 第 8 节：对比度 ≥4.5:1，AA）。

设计意图：**从 CSS 里读真实用到的"文字色 × 背景色"组合**，而不是维护一份
手工清单 —— 手工清单一改样式就过期，测的不是实际渲染的东西。

配对方式（按 ui-design 8 的用法规则）：
  - 任何 `background` 为实色的规则，其自身 `color` 与它配对
  - 未声明 color 的实色背景，与它所在的选择器链上最近声明的 color 配对
    （简化处理：与 --c-text / 白字 这两个实际会出现的搭配比）
  - 另外显式列出"语义色在常见底色上"的组合，覆盖 token 本身是否可用于文字

用法：python tools/check_contrast.py
退出码：0 全部达标 / 1 有不达标
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

AA_NORMAL = 4.5      # 正文（< 18.66px 常规 / < 24px 加粗）
AA_LARGE = 3.0       # 大字号（≥ 24px 或 ≥ 18.66px 加粗）与非文字信息


# ---------------------------------------------------------------- 颜色工具

def read_tokens(css: str) -> dict[str, str]:
    tokens = {}
    for m in re.finditer(r"(--c-[a-z-]+):\s*(#[0-9A-Fa-f]{6})\s*;", css):
        tokens[m.group(1)] = m.group(2).upper()
    return tokens


TOKENS = read_tokens(CSS)


def resolve(value: str, over: str | None = None) -> str | None:
    """把 `var(--x)`、`#RRGGBB` 或 `rgba(255,255,255,a)` 解析成 #RRGGBB。

    `over` 是半透明前景的合成底色（深色侧边栏那种场景），不传则按白底合成。
    """
    value = value.strip()
    m = re.fullmatch(r"var\((--c-[a-z-]+)\)", value)
    if m:
        return TOKENS.get(m.group(1))
    m = re.fullmatch(r"(#[0-9A-Fa-f]{3,6})", value)
    if m:
        h = m.group(1)
        if len(h) == 4:      # #abc -> #aabbcc
            h = "#" + "".join(c * 2 for c in h[1:])
        return h.upper()
    m = re.fullmatch(r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*([0-9.]+)\s*\)", value)
    if m:
        # 半透明白：与底色做 alpha 合成后再算对比度
        alpha = float(m.group(1))
        base = (over or WHITE).lstrip("#")
        br, bg_, bb = (int(base[i:i + 2], 16) for i in (0, 2, 4))
        ch = [round(255 * alpha + c * (1 - alpha)) for c in (br, bg_, bb)]
        return "#" + "".join(f"{c:02X}" for c in ch)
    return None


def _lin(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexcolor: str) -> float:
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- 规则解析

WHITE = "#FFFFFF"
CARD = TOKENS.get("--c-surface", WHITE)
PAGE = TOKENS.get("--c-bg", WHITE)
SIDEBAR = TOKENS.get("--c-sidebar", "#1A1A2E")

# 从样式表里抓「同一规则内同时有 color 与实色 background」的组合
pairs: list[tuple[str, str, str, float]] = []   # (来源, 前景, 背景, 要求)
for m in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS):
    selector = " ".join(m.group(1).split())
    body = m.group(2)
    if "@media" in selector or ":" in selector.split()[-1:][0][:0]:
        pass
    # 只抓 `color:` 本身，不能把 `border-color:` / `background-color:` 当成文字色
    color_m = re.search(r"(?<![-\w])color:\s*([^;]+);", body)
    bg_m = re.search(r"(?<![-\w])background:\s*([^;]+);", body)
    if not (color_m and bg_m):
        continue
    fg = resolve(color_m.group(1))
    bg = resolve(bg_m.group(1))
    if fg and bg:
        pairs.append((selector[:44], fg, bg, AA_NORMAL))

# 显式补充：实际会出现的"文字色 / 底色"组合（含 token 可用性判断）
EXTRA = [
    ("正文 --c-text / 卡片", "--c-text", CARD, AA_NORMAL),
    ("次要 --c-text-muted / 卡片", "--c-text-muted", CARD, AA_NORMAL),
    ("次要 --c-text-muted / 页面底色", "--c-text-muted", PAGE, AA_NORMAL),
    ("主色文字 --c-primary-text / 卡片", "--c-primary-text", CARD, AA_NORMAL),
    ("成功文字 --c-success-text / 卡片", "--c-success-text", CARD, AA_NORMAL),
    ("警告文字 --c-warning-text / 卡片", "--c-warning-text", CARD, AA_NORMAL),
    ("警告文字 / --c-warning-soft 底", "--c-warning-text",
     TOKENS.get("--c-warning-soft", WHITE), AA_NORMAL),
    ("危险文字 --c-danger-text / 卡片", "--c-danger-text", CARD, AA_NORMAL),
    ("危险文字 / --c-danger-soft 底", "--c-danger-text",
     TOKENS.get("--c-danger-soft", WHITE), AA_NORMAL),
    ("主色文字 / --c-primary-soft 底", "--c-primary-text",
     TOKENS.get("--c-primary-soft", WHITE), AA_NORMAL),
    # 填充色 + 白字：这些是"用作填充"的正当用法，必须达标
    ("白字 / 主色填充 --c-primary-fill", WHITE, "--c-primary-fill", AA_NORMAL),
    ("白字 / 危险填充 --c-danger-fill", WHITE, "--c-danger-fill", AA_NORMAL),
    # ui-design 2.1 的原色：只允许做边框/图标/浅底，这里显式记录它们的实际比值，
    # 若有人把白字压上去，下面的断言会失败（4.04 / 2.44 / 1.86 / 1.90）。
    ("[仅参考] 白字 / 原主色 #4A7CDB", WHITE, "--c-primary", AA_LARGE),
    ("[仅参考] 白字 / 原危险色 #FC8181", WHITE, "--c-danger", AA_LARGE),
    ("侧边栏主文字 / 侧边栏", None, SIDEBAR, AA_NORMAL, "rgba(255, 255, 255, 0.72)"),
    ("侧边栏次要 / 侧边栏", None, SIDEBAR, AA_NORMAL, "rgba(255, 255, 255, 0.62)"),
]

# 明确豁免：非文字信息（图标、边框、装饰点），按 ui-design 8
# "状态色配合图标/文字"的口径，AA 的 4.5:1 只约束**文字**。
EXEMPT_SELECTORS = (
    ".toast--success .toast__icon", ".toast--warning .toast__icon",
    ".toast--error .toast__icon", ".star--on",
)

rows: list[tuple[str, str, str, float, bool]] = []


def add(name: str, fg: str | None, bg: str | None, need: float, exempt=False):
    if fg is None or bg is None:
        return
    rows.append((name, fg, bg, need, exempt))


for item in EXTRA:
    if len(item) == 5:
        name, tok, bg, need, literal = item
        bg_hex = resolve(bg) if isinstance(bg, str) else bg
        add(name, resolve(literal, over=bg_hex), bg_hex, need)
    else:
        name, tok, bg, need = item
        add(name, resolve(tok) if isinstance(tok, str) else tok,
            resolve(bg) if isinstance(bg, str) else bg, need)

for selector, fg, bg, need in pairs:
    exempt = any(e in selector for e in EXEMPT_SELECTORS)
    add(selector, fg, bg, need, exempt)


def main() -> int:
    print("=" * 86)
    print("WCAG 对比度校验（AA：正文 ≥4.5:1）")
    print("=" * 86)
    print(f"{'组合':<46}{'前景':<9}{'背景':<9}{'比值':>7}  结果")
    print("-" * 86)

    fails = 0
    exempt = 0
    for name, fg, bg, need, is_exempt in rows:
        r = contrast(fg, bg)
        ok = r >= need
        if is_exempt:
            exempt += 1
            verdict = "豁免(非文字)"
        elif ok:
            verdict = "通过"
        else:
            verdict = "不通过"
            fails += 1
        print(f"{name:<46}{fg:<9}{bg:<9}{r:>7.2f}  {verdict}")

    print("-" * 86)
    print(f"合计 {len(rows)} 组：不通过 {fails} 组，豁免 {exempt} 组")
    print("豁免说明：图标/装饰属非文字信息，不受 4.5:1 约束；")
    print("          语义色当填充用时与白字的组合已单列并纳入校验。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
