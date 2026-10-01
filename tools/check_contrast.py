"""计算状态色与背景的对比度，核对 ui-design 8「对比度 ≥4.5:1（WCAG AA）」。

只算实际用到的组合，不猜。
"""

import sys


def srgb_to_lin(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def lum(hexcolor: str) -> float:
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * srgb_to_lin(r) + 0.7152 * srgb_to_lin(g) + 0.0722 * srgb_to_lin(b)


def ratio(fg: str, bg: str) -> float:
    a, b = lum(fg), lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


WHITE = "#FFFFFF"
BG = "#F5F7FA"      # 全局背景
SURFACE = "#FFFFFF"  # 卡片
SIDEBAR = "#1A1A2E"  # 侧边栏

CASES = [
    # (用途, 前景, 背景, 要求)
    ("正文 #2D3748 / 卡片", "#2D3748", SURFACE, 4.5),
    ("次要文字 #718096 / 卡片", "#718096", SURFACE, 4.5),
    ("辅助文字 #A0AEC0 / 卡片", "#A0AEC0", SURFACE, 4.5),
    ("辅助文字 #A0AEC0 / 全局背景", "#A0AEC0", BG, 4.5),
    ("主色 #4A7CDB / 卡片", "#4A7CDB", SURFACE, 4.5),
    ("辅助色 #7B68EE / 卡片", "#7B68EE", SURFACE, 4.5),
    ("成功色 #68D391 / 卡片", "#68D391", SURFACE, 4.5),
    ("警告色 #F6AD55 / 卡片", "#F6AD55", SURFACE, 4.5),
    ("危险色 #FC8181 / 卡片", "#FC8181", SURFACE, 4.5),
    ("白字 / 主色按钮", WHITE, "#4A7CDB", 4.5),
    ("白字 / 危险按钮", WHITE, "#FC8181", 4.5),
    ("白字 / 成功色", WHITE, "#68D391", 4.5),
    ("白字 / 警告色", WHITE, "#F6AD55", 4.5),
    ("侧边栏主文字(72%白) / 侧边栏", "#B8B8C4", SIDEBAR, 4.5),
    ("侧边栏次要(45%白) / 侧边栏", "#7A7A88", SIDEBAR, 4.5),
    ("侧边栏激活白字 / 主色", WHITE, "#4A7CDB", 4.5),
    # 标签类实际用色
    ("标签--warning 文字 #B7791F / 警告底色", "#B7791F", "#FBEEDC", 4.5),
    ("标签--hard 文字 #C53030 / 危险底色", "#C53030", "#FDE8E8", 4.5),
    ("标签--reward 文字 #2F855A / 成功底色", "#2F855A", "#E6F6EC", 4.5),
    ("badge 白字 / 危险色", WHITE, "#FC8181", 4.5),
    ("badge 白字 / 警告色", WHITE, "#F6AD55", 4.5),
]

fails = 0
print(f"{'用途':<38} {'前景':<9} {'背景':<9} {'比值':>6}  要求  结果")
print("-" * 82)
for name, fg, bg, need in CASES:
    r = ratio(fg, bg)
    ok = r >= need
    if not ok:
        fails += 1
    print(f"{name:<38} {fg:<9} {bg:<9} {r:>6.2f}  {need:>4}  {'通过' if ok else '不通过'}")

print("-" * 82)
print(f"合计 {len(CASES)} 组，不通过 {fails} 组")
sys.exit(0)
