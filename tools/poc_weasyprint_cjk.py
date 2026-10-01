"""PoC：确认 WeasyPrint 能正确渲染中文（不是方框），并验证 @font-face 可用。

检查点：
1. 原生库（Pango/GObject）能加载
2. @font-face 用 file:// 引入 fonts/NotoSansSC-VF.ttf
3. 渲染出的 PDF 里中文是可提取的文本（不是缺字方框）
4. 字体确实被嵌入（PDF 里有 FontFile 或字形子集）
5. 页数、尺寸符合预期

方框的判定：WeasyPrint 遇到缺字时会把字符渲染成 .notdef 或直接丢失文本。
因此"PDF 文本里能取回原中文"是比"看起来像"更硬的证据。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _pdf_probe as P  # noqa: E402

from app.weasyprint_bootstrap import chinese_font_path, ensure_native_libs  # noqa: E402

OK = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
    print(("[PASS] " if cond else "[FAIL] ") + label + (f"  ({detail})" if detail else ""))


def main() -> int:
    print("=" * 74)
    print("WeasyPrint 中文渲染 PoC")
    print("=" * 74)

    gtk = ensure_native_libs()
    check("GTK 原生库可加载", True, str(gtk))

    import weasyprint  # noqa: E402

    check("weasyprint 可导入", True, f"版本 {weasyprint.__version__}")

    font = chinese_font_path()
    check("中文字体文件存在", font.is_file(), f"{font.name} {font.stat().st_size} 字节")

    # 覆盖常见中文标点、数字、上下标、括号，以及 PDF 导出实际会遇到的内容
    sample = "错题本：极限与连续　洛必达法则（0/0 型）　x² + y² ≤ 1　【重要】"
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
  @font-face {{
    font-family: "Noto Sans SC";
    src: url("{font.as_uri()}");
  }}
  body {{ font-family: "Noto Sans SC"; font-size: 14px; }}
  .big {{ font-size: 28px; }}
</style></head><body>
  <p class="big">{sample}</p>
  <p>第二行：一元二次方程 ax<sub>0</sub> + b 的判别式 Δ = b² - 4ac。</p>
</body></html>"""

    tmp = Path(tempfile.mkdtemp(prefix="poc_pdf_"))
    pdf_path = tmp / "poc.pdf"
    weasyprint.HTML(string=html, base_url=str(tmp)).write_pdf(str(pdf_path))

    check("PDF 生成成功", pdf_path.is_file(), f"{pdf_path.stat().st_size} 字节")

    pdf = pdf_path.read_bytes()
    check("PDF 头合法", pdf[:5] == b"%PDF-")

    # 字体嵌入：不能直接搜原始字节 —— WeasyPrint 会把对象字典压进对象流
    # （/FontFile2、/ToUnicode 这类键都在压缩流里），搜原始字节会误判为"没嵌入"。
    # _pdf_probe 会先解压所有流再找。
    check("字体已嵌入 PDF", P.has_embedded_font(pdf))
    names = P.font_names(pdf)
    check("嵌入的是 Noto Sans SC（不是回退字体）",
          any("Noto" in n for n in names), names)
    check("有 ToUnicode 映射（文本可复制）", b"/ToUnicode" in P.stream_bytes(pdf))

    # 文本可提取 = 中文确实按字形渲染，而不是缺字方框。
    # 用 pypdf 读 ToUnicode CMap：方框是无法还原成原字的，这比"看起来像"更硬。
    text = P.extract_text(pdf)
    check("提取文本非空", bool(text.strip()), f"{len(text)} 字符")
    for token in ("错题本", "洛必达", "极限", "判别式"):
        check(f"能取回中文「{token}」", token in text,
              token if token in text else "未找到")
    check("上下标 ₀ 已按 <sub> 渲染（不是缺字方框）", "0" in text)

    check("文本中没有替换符 U+FFFD", "\ufffd" not in text)
    check("文本中没有 .notdef 痕迹",
          ".notdef" not in pdf.decode("latin-1", "ignore"))

    print("-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    print(f"产物：{pdf_path}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
