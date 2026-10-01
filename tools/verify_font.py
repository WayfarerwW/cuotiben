"""WeasyPrint 中文渲染验证（PoC 结论的可重复检验）。

产出：
  - poc_out/poc_cn_sample.pdf   样张 PDF（中文、标点、数学符号、生僻字、表格、粗体）
  - poc_out/poc_cn_sample.png   第 1 页光栅图，便于肉眼确认无方框
  - 控制台报告：字体覆盖率、嵌入状态、缺字情况

判定逻辑（不靠 PDF 体积猜测）：
  1. 用 pypdf 读 PDF 的字体描述符，确认 /FontFile2|3 存在（字体真的内嵌）
  2. 用 pypdfium2 提取文本，确认探针字符全在（字体与 cmap 正确）
  3. 光栅化出 PNG，供人工核对没有豆腐块

运行：python tools/verify_font.py
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "poc_out"
FONT = ROOT / "fonts" / "NotoSansSC-VF.ttf"

# 探针：常见字 + 全角标点 + 数学符号 + 生僻字（错题本实际会遇到的内容）
PROBE = ("错题本函数极限连续导数微分积分级数矩阵特征值洛必达法则"
         "（），。、；：？！“”《》【】απ∞≈≤≥≠∑√"
         "龘齉靐鱻麤爨蠹饕餮黼黻夔犇猋驫")

SAMPLE = """<!doctype html><html><head><meta charset="utf-8">
<style>
  @font-face { font-family: "CuotibenCN"; src: url("file:///__FONT__"); }
  @page { size: A4; margin: 18mm; }
  body { font-family: "CuotibenCN"; font-size: 12pt; line-height: 1.8; color: #1f2328; }
  h1 { font-size: 18pt; margin: 0 0 6mm 0; }
  h2 { font-size: 13pt; margin: 6mm 0 2mm 0; color: #0b5cad; }
  .k { color: #b00020; }
  .muted { color: #6b7280; font-size: 10pt; }
  table { border-collapse: collapse; width: 100%; }
  td, th { border: 1px solid #d0d7de; padding: 2mm 3mm; text-align: left; }
  .sub { font-size: 8pt; }
</style></head><body>
<h1>错题本 · PDF 中文渲染样张</h1>
<p class="muted">字体：fonts/NotoSansSC-VF.ttf（SIL OFL 1.1）　WeasyPrint + MSYS2 UCRT64 GTK</p>

<h2>1. 正文与标点</h2>
<p>函数极限与连续、导数、微分、积分、级数、矩阵、特征值。设为 <span class="k">f(x)</span>
在点 x 处可导，则 f 在该点必连续；反之未必成立。全角标点：（），。、；：？！“”‘’——……《》【】</p>

<h2>2. 数学符号（错题本高频）</h2>
<p>lim<sub class="sub">x→0</sub> sinx/x = 1　　∫<sub class="sub">0</sub><sup>1</sup> f(x)dx = 1/2　　
α ∈ (0, π)　　x² + y² = 1　　∞　　≈　　≤　　≥　　≠　　∑　　√</p>
<p class="muted">注：下标用 &lt;sub&gt; 标签，不要用 U+2080 ₀ —— 所有中文字体都缺该字符。</p>

<h2>3. 生僻字抽检</h2>
<p>龘 齉 靐 鱻 麤 爨 蠹 饕 餮 黼 黻 夔 犇 猋 驫</p>

<h2>4. 表格（题干/答案排版用）</h2>
<table>
<tr><th>知识点</th><th>易错点</th><th>状态</th></tr>
<tr><td>洛必达法则</td><td>仅适用于 0/0 或 ∞/∞ 型</td><td>仍易错</td></tr>
<tr><td>等价无穷小替换</td><td>加减法中不可直接替换</td><td>已拿下</td></tr>
<tr><td>定积分换元</td><td>上下限必须同步换元</td><td>仍易错</td></tr>
</table>

<h2>5. 粗体与字号</h2>
<p><b>粗体中文测试：重点题标记与强调文本。</b></p>
<p style="font-size:9pt">9pt 小字　<span style="font-size:11pt">11pt</span>　<span style="font-size:16pt">16pt</span></p>
</body></html>"""


def embedded_fonts(pdf: bytes) -> list[tuple[str, bool]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    out: list[tuple[str, bool]] = []
    for page in reader.pages:
        fonts = (page.get("/Resources") or {}).get("/Font")
        if fonts is None:
            continue
        for k in list(fonts.keys()):
            fo = fonts[k].get_object()
            base = str(fo.get("/BaseFont", "?"))
            emb = False
            cands = ([d.get_object() for d in fo["/DescendantFonts"]]
                     if "/DescendantFonts" in fo else [fo])
            for c in cands:
                fd = c.get("/FontDescriptor")
                if fd is None:
                    continue
                fd = fd.get_object()
                emb = any(x in fd for x in ("/FontFile", "/FontFile2", "/FontFile3"))
            if base not in [b for b, _ in out]:
                out.append((base, emb))
    return out


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 78)
    print("WeasyPrint 中文渲染验证")
    print("=" * 78)

    failures: list[str] = []

    # 1. 原生库引导
    try:
        from app.weasyprint_bootstrap import (chinese_font_path, ensure_native_libs,
                                              find_gtk_bin)
    except ImportError:
        sys.path.insert(0, str(ROOT / "app"))
        from weasyprint_bootstrap import (chinese_font_path, ensure_native_libs,
                                          find_gtk_bin)

    gtk = find_gtk_bin()
    if gtk:
        print(f"[PASS] 找到 GTK 运行库 — {gtk}")
    else:
        print("[FAIL] 未找到 GTK 运行库（libgobject-2.0-0.dll）")
        print("       修复方式见 docs/poc-weasyprint.md")
        return 1

    try:
        ensure_native_libs()
        import weasyprint
        print(f"[PASS] import weasyprint — 版本 {weasyprint.__version__}")
    except Exception as e:
        print(f"[FAIL] import weasyprint — {type(e).__name__}: {str(e).splitlines()[0][:100]}")
        return 1

    # 2. 字体文件
    try:
        font = chinese_font_path()
        print(f"[PASS] 中文字体 {font.relative_to(ROOT)} — {font.stat().st_size/1024/1024:.1f} MB")
    except Exception as e:
        print(f"[FAIL] {e}")
        return 1

    # 3. 字体覆盖率（cmap 判定，权威）
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(str(font), lazy=True, fontNumber=0)
        cmap = tt.getBestCmap() or {}
        missing = [c for c in PROBE if ord(c) not in cmap]
        is_var = "fvar" in tt
        tt.close()
        if missing:
            print(f"[FAIL] 字体覆盖 — 缺 {len(missing)} 字: {''.join(missing[:20])}")
            failures.append("字体覆盖率")
        else:
            print(f"[PASS] 字体覆盖 — {len(PROBE)} 个探针字符全覆盖"
                  f"{'（可变字体）' if is_var else ''}")
    except Exception as e:
        print(f"[FAIL] 字体覆盖检查 — {type(e).__name__}: {str(e)[:90]}")
        failures.append("字体覆盖检查")

    # 4. 渲染样张
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration

    fc = FontConfiguration()
    html = SAMPLE.replace("__FONT__", font.as_posix())
    pdf = HTML(string=html, base_url=str(ROOT)).write_pdf(font_config=fc)
    pdf_path = OUT_DIR / "poc_cn_sample.pdf"
    pdf_path.write_bytes(pdf)
    print(f"[PASS] 渲染样张 PDF — {len(pdf)/1024:.1f} KB → {pdf_path.name}")

    # 5. 字体是否真的嵌入
    fonts = embedded_fonts(pdf)
    not_embedded = [b for b, e in fonts if not e]
    if fonts and not not_embedded:
        print(f"[PASS] 字体已嵌入 — {[b.split('+')[-1] for b, _ in fonts]}")
    else:
        print(f"[FAIL] 字体未完全嵌入 — 未嵌入: {not_embedded}")
        failures.append("字体嵌入")

    # 6. 文本提取（缺字判据）+ 光栅图
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(pdf)
        txt = "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
        miss = [c for c in PROBE if c not in txt]
        if miss:
            print(f"[FAIL] 文本提取缺失 {len(miss)} 字: {''.join(miss[:20])}")
            failures.append("文本提取")
        else:
            print(f"[PASS] 文本提取 — {len(doc)} 页，探针字符无缺失")
        for i in range(len(doc)):
            img = doc[i].render(scale=2.2).to_pil()
            png = OUT_DIR / f"poc_cn_sample_p{i+1}.png"
            img.save(png)
            print(f"[PASS] 光栅图 {png.name} — {img.size}")
        doc.close()
    except Exception as e:
        print(f"[FAIL] 文本提取/光栅化 — {type(e).__name__}: {str(e)[:90]}")
        failures.append("文本提取")

    print("-" * 78)
    if failures:
        print(f"失败项: {failures}")
        return 1
    print("全部通过：中文可正常渲染且字体已内嵌，PDF 可脱离本机字体环境查看/打印。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
