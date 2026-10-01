"""PDF 检查辅助（仅测试用）。

为什么单独抽一个模块：PDF 文本提取很容易写错。
第一版用正则抓 `<hex> Tj`，结果全空 —— WeasyPrint 生成的是 **CID 字体**，
内容流里是字形 id，真正的文字在 ToUnicode CMap 里，必须按映射表还原。
后来发现环境里已经有 `pypdf`（它本身就是 WeasyPrint 的依赖），
用成熟解析器比自己写正则可靠得多，也让测试断言的是"PDF 里到底写了什么"。

不要在生产代码里 import 本模块。
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

__all__ = [
    "extract_text",
    "page_count",
    "font_names",
    "stream_bytes",
    "has_embedded_font",
]


def _blob(source: bytes | Path) -> bytes:
    return source.read_bytes() if isinstance(source, Path) else source


def stream_bytes(source: bytes | Path) -> bytes:
    """解压所有流后与原始字节拼接。

    PDF 的对象字典会被压进对象流，直接在原始字节里搜 `/FontFile2`、
    `/ToUnicode` 这类键会找不到 —— 必须解压后再搜。
    """
    raw = _blob(source)
    out = bytearray(raw)
    for chunk in re.findall(rb"stream\r?\n(.*?)endstream", raw, re.S):
        out += chunk
        try:
            out += zlib.decompress(chunk.strip(b"\r\n"))
        except Exception:
            pass
    return bytes(out)


def page_count(source: bytes | Path) -> int:
    """页数。优先用 pypdf，失败时退回数 /Type /Page。"""
    try:
        import io

        from pypdf import PdfReader

        data = _blob(source)
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return len(re.findall(rb"/Type\s*/Page[^s]", stream_bytes(source)))


def extract_text(source: bytes | Path) -> str:
    """提取 PDF 文本（按页拼接）。

    pypdf 会读 ToUnicode CMap，因此中文能正确还原；
    这也正是"中文不是方框"最硬的证据：方框是无法还原成原字的。
    """
    import io

    from pypdf import PdfReader

    data = _blob(source)
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def font_names(source: bytes | Path) -> list[str]:
    """PDF 里出现的字体名（含子集前缀如 ABCDEF+NotoSansSC）。"""
    names: set[str] = set()
    blob = stream_bytes(source)
    for m in re.finditer(rb"/BaseFont\s*/([A-Za-z0-9+\-_,\.]+)", blob):
        names.add(m.group(1).decode("latin-1"))
    return sorted(names)


def has_embedded_font(source: bytes | Path) -> bool:
    blob = stream_bytes(source)
    return any(k in blob for k in (b"/FontFile2", b"/FontFile3", b"/FontFile"))
