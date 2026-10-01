"""WeasyPrint 原生库（Pango/GObject/Cairo）引导。

背景
----
WeasyPrint 依赖 Pango、GObject、Cairo、gdk-pixbuf、fontconfig 等原生库。
这些库不随 pip 分发，Windows 上必须另行提供。本项目通过 MSYS2 的 UCRT64
环境提供（安装步骤见 docs/poc-weasyprint.md）。

本模块把 GTK 运行库目录加入本进程的 DLL 搜索路径，让 MSVC 构建的 CPython
能加载 MinGW 构建的 GTK 库。

为什么需要显式引导
------------------
实测：MSYS2 默认装在 C:\\msys64 时，即使 PATH 为空 WeasyPrint 也能找到库
（Windows 的 DLL 解析在起作用）。但把 C:\\msys64\\ucrt64 改名后立即报
`OSError: cannot load library 'libgobject-2.0-0'`。
即在"默认安装位置"能碰巧工作，换个安装目录就不成立。
因此 run.py 在最早时机显式调用本模块，把行为固定下来，不依赖偶然。

用法
----
    from app.weasyprint_bootstrap import ensure_native_libs
    ensure_native_libs()
    # 之后再 import weasyprint

环境变量（可选）
----------------
    CUOTIBEN_GTK_BIN  直接指定含 libgobject-2.0-0.dll 的目录
    MSYS2_ROOT        指定 MSYS2 安装根目录（默认 C:\\msys64）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_MSYS2_ROOT = r"C:\msys64"
_REQUIRED_DLL = "libgobject-2.0-0.dll"

_cached: Path | None = None


def candidate_dirs() -> list[Path]:
    """按优先级列出候选 GTK bin 目录。"""
    dirs: list[Path] = []

    env_bin = os.environ.get("CUOTIBEN_GTK_BIN")
    if env_bin:
        dirs.append(Path(env_bin))

    roots = [Path(os.environ.get("MSYS2_ROOT", DEFAULT_MSYS2_ROOT))]
    if os.environ.get("MSYS2_ROOT") != DEFAULT_MSYS2_ROOT:
        roots.append(Path(DEFAULT_MSYS2_ROOT))

    for root in roots:
        # ucrt64 优先：与 MSVC 版 CPython 同为 UCRT 运行时，兼容性最好
        dirs.append(root / "ucrt64" / "bin")
        dirs.append(root / "mingw64" / "bin")

    return dirs


def find_gtk_bin() -> Path | None:
    """返回第一个确实含 libgobject 的目录；找不到返回 None。"""
    for d in candidate_dirs():
        try:
            if (d / _REQUIRED_DLL).is_file():
                return d
        except OSError:
            continue
    return None


def ensure_native_libs(verbose: bool = False) -> Path:
    """把 GTK 运行库目录加入 DLL 搜索路径。幂等，可重复调用。

    返回实际使用的目录；找不到可用的 GTK 运行库时抛 RuntimeError。
    """
    global _cached
    if _cached is not None:
        return _cached

    gtk_bin = find_gtk_bin()
    if gtk_bin is None:
        raise RuntimeError(
            "未找到 WeasyPrint 所需的 Pango/GObject 运行库。\n"
            "PDF 导出功能不可用，其余功能不受影响。\n"
            "修复方式见 docs/poc-weasyprint.md：安装 MSYS2 与 GTK 依赖，"
            "或设置环境变量 CUOTIBEN_GTK_BIN 指向含 "
            f"{_REQUIRED_DLL} 的目录。"
        )

    # 方式一：PATH 前置。cffi 的 dlopen 在 Windows 上会走 PATH 搜索，
    # 这是 WeasyPrint 官方 Windows 方案依赖的机制。
    current = os.environ.get("PATH", "")
    if str(gtk_bin) not in current.split(os.pathsep):
        os.environ["PATH"] = str(gtk_bin) + os.pathsep + current

    # 方式二：Python 3.8+ 的 DLL 目录 API。对 cffi.dlopen 不一定生效，补上无害。
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(str(gtk_bin))
        except OSError:
            pass

    _cached = gtk_bin
    if verbose:
        print(f"[weasyprint_bootstrap] GTK bin = {gtk_bin}", file=sys.stderr)
        print(f"[weasyprint_bootstrap] python  = {sys.executable}", file=sys.stderr)
    return gtk_bin


def project_fonts_dir() -> Path:
    """项目 fonts/ 目录（PDF 中文字体所在）。"""
    return Path(__file__).resolve().parent.parent / "fonts"


def chinese_font_path() -> Path:
    """PDF 导出使用的中文字体文件。

    选用 Noto Sans SC（SIL OFL 1.1，可自由分发），随仓库放在 fonts/。
    不依赖 C:\\Windows\\Fonts 里的微软字体，避免授权与可移植性问题。
    """
    p = project_fonts_dir() / "NotoSansSC-VF.ttf"
    if not p.is_file():
        raise FileNotFoundError(
            f"缺少中文字体文件 {p}。PDF 中文将无法正确嵌入，"
            "请参考 fonts/NotoSansSC-OFL.txt 说明补齐字体。"
        )
    return p
