"""HEIC 读写与图片压缩管线验证（PoC 结论的可重复检验）。

覆盖需求 2.6 / 2.7 与第 6 节风险点：
  - pillow-heif 能真正读写 HEIC（libheif 编解码器可用）
  - 统一宽 1080px、等比不变形
  - 转 JPEG q75、单图 ≤ 300KB
  - 显式 open_heif 解码路径可用

运行：python tools/verify_heic.py
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

# 控制台默认 cp936，中文输出会抛 UnicodeEncodeError 或被 mojibake，必须强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = Path(__file__).resolve().parent.parent / "poc_out"

TARGET_WIDTH = 1080
JPEG_QUALITY = 75
MAX_BYTES = 300 * 1024

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def compress_uniform(file_bytes: bytes, target_width: int = TARGET_WIDTH,
                     quality: int = JPEG_QUALITY) -> tuple[bytes, tuple[int, int]]:
    """需求 5.6 节的压缩实现。返回 (jpeg_bytes, 最终尺寸)。"""
    from PIL import Image

    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    w, h = img.size
    if w != target_width:
        ratio = target_width / w
        img = img.resize((target_width, int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), img.size


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 68)
    print("HEIC 读写验证")
    print("=" * 68)

    try:
        import pillow_heif
        from PIL import Image, ImageDraw
    except Exception:
        traceback.print_exc()
        check("导入 pillow_heif / PIL", False, "依赖缺失")
        return 1

    check("导入 pillow_heif / PIL", True,
          f"pillow_heif {pillow_heif.__version__}, Pillow {Image.__version__}")

    # 注意：Pillow 本身不含 HEVC 编解码器，features.check('heif') 之类的开关
    # 与此无关；真正的能力来自 pillow-heif 内置的 libheif，用 libheif_info() 判定。
    info = pillow_heif.libheif_info()
    print(f"       libheif {pillow_heif.libheif_version()}")
    print(f"       编码器: {info.get('encoders')}")
    print(f"       解码器: {info.get('decoders')}")
    check("libheif 具备 HEVC 解码器", bool(info.get("decoders")),
          f"{list((info.get('decoders') or {}).keys())}")
    check("libheif 具备 HEVC 编码器", bool(info.get("encoders")),
          f"{list((info.get('encoders') or {}).keys())}")

    pillow_heif.register_heif_opener()

    # 模拟手机竖拍 4032x3024
    src_w, src_h = 4032, 3024
    src = Image.new("RGB", (src_w, src_h), (250, 248, 244))
    d = ImageDraw.Draw(src)
    for i in range(0, src_w, 200):
        d.line([(i, 0), (i, src_h)], fill=(200, 210, 220), width=3)
    for j in range(0, src_h, 200):
        d.line([(0, j), (src_w, j)], fill=(200, 210, 220), width=3)
    d.ellipse([400, 300, 1600, 1500], fill=(90, 130, 200))
    src.save(OUT_DIR / "src_4032x3024.png", "PNG")
    check("构造源图", True, f"{src_w}x{src_h}")

    # 写 HEIC
    heic_path = OUT_DIR / "sample_we_wrote.heic"
    try:
        buf = io.BytesIO()
        src.save(buf, format="HEIF", quality=80)
        heic_path.write_bytes(buf.getvalue())
        check("写 HEIC (Pillow format='HEIF')", True,
              f"{len(buf.getvalue())/1024:.0f} KB")
    except Exception as e:
        check("写 HEIC (Pillow format='HEIF')", False, f"{type(e).__name__}: {e}")

    # 读回 + 压缩管线
    if heic_path.is_file():
        raw = heic_path.read_bytes()
        try:
            img = Image.open(io.BytesIO(raw))
            check("读 HEIC", img.format == "HEIF" and img.size == (src_w, src_h),
                  f"format={img.format} size={img.size} mode={img.mode}")
        except Exception as e:
            check("读 HEIC", False, f"{type(e).__name__}: {e}")

        try:
            jpg, out_size = compress_uniform(raw)
            (OUT_DIR / "compressed.jpg").write_bytes(jpg)
            check("HEIC → 压缩管线", out_size[0] == TARGET_WIDTH,
                  f"{src_w}x{src_h} → {out_size}, {len(jpg)/1024:.0f} KB, JPEG q{JPEG_QUALITY}")
            check("压缩后 ≤ 300KB", len(jpg) <= MAX_BYTES,
                  f"{len(jpg)/1024:.0f} KB / 300 KB 上限")
            expect_h = round(src_h * (TARGET_WIDTH / src_w))
            check("不变形(等比)", abs(out_size[1] - expect_h) <= 1,
                  f"期望高 {expect_h}, 实际 {out_size[1]}")
        except Exception as e:
            check("HEIC → 压缩管线", False, f"{type(e).__name__}: {e}")

        # 显式 open_heif 路径（不依赖 register_heif_opener）
        try:
            heif = pillow_heif.open_heif(raw, convert_hdr_to_8bit=True)
            check("显式 open_heif 解码", heif[0].size == (src_w, src_h),
                  f"size={heif[0].size} mode={heif[0].mode}")
        except Exception as e:
            check("显式 open_heif 解码", False, f"{type(e).__name__}: {e}")

    # 真实样张（若本机存在）
    found = []
    for root in (Path.home() / "Pictures", Path.home() / "Downloads", Path.home() / "Desktop"):
        if root.exists():
            found += list(root.rglob("*.heic"))[:3] + list(root.rglob("*.HEIC"))[:3]
    if found:
        for f in found[:3]:
            try:
                with Image.open(f) as im:
                    im.load()
                    check(f"读真实 HEIC {f.name}", True, f"{im.size} {im.mode}")
            except Exception as e:
                check(f"读真实 HEIC {f.name}", False, f"{type(e).__name__}: {e}")
    else:
        print("[SKIP] 未找到真实 .heic 样张（合成图已验证，真机照片建议再抽验一次）")

    print("-" * 68)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    print(f"产物目录: {OUT_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
