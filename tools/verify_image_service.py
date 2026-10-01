"""图片压缩与上传自检（requirements.md 2.6 / 2.7 / 5.6，AGENTS.md 4.4）。

覆盖：
  - 显式 register_heif_opener()
  - convert('RGB')、等比缩放到宽 1080px、不变形不裁剪
  - JPEG quality=75 optimize=True、单图 ≤ 300KB
  - 压缩失败回退原图且不阻断上传
  - 存储：uploads/YYYY/MM/DD/{uuid}.jpg，返回相对路径
  - 路径 <-> URL 互转、静态挂载可访问
  - POST /upload/image 经真实 HTTP（TestClient）：返回 url/file_path/width/height/size
  - 校验：非法扩展名 400、超 10MB 413、空文件 400

走临时数据库；图片写入真实 uploads/ 目录，测试结束清理自己产生的文件。

运行：python tools/verify_image_service.py
"""

from __future__ import annotations

import io
import os
import random
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _to_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _encode_size(img, quality: int) -> int:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return len(buf.getvalue())


def make_speckled(width: int, height: int, density: float = 0.1, seed: int = 0,
                  amp: int = 90):
    """造一张"真实照片感"的图：平滑渐变底 + 按 density 比例撒噪点。

    纯渐变压缩率过高（几十 KB）、纯噪声又完全不可压缩（几百 KB），
    两者都测不出真实行为，所以混合：density 用来调"细节量"，
    从而得到落在 300KB 附近的真实照片量级样本。
    """
    import random as _random

    from PIL import Image

    rnd = _random.Random(seed)
    img = Image.new("RGB", (width, height))
    px = img.load()
    for y in range(height):
        for x in range(width):
            base = ((x * 3) % 256, (y * 2) % 256, ((x + y) * 5) % 256)
            if rnd.random() < density:
                delta = rnd.randint(-amp, amp)
                base = tuple(max(0, min(255, c + delta)) for c in base)
            px[x, y] = base
    return img


def make_image(width: int, height: int, fmt: str = "JPEG", color=(90, 130, 200)) -> bytes:
    """造一张有内容的测试图（纯色会被压缩得太小，加些纹理更接近真实照片）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (250, 248, 244))
    d = ImageDraw.Draw(img)
    for i in range(0, width, 60):
        d.line([(i, 0), (i, height)], fill=color, width=4)
    for j in range(0, height, 60):
        d.line([(0, j), (width, j)], fill=(200, 210, 220), width=4)
    d.ellipse([width // 10, height // 10, width // 2, height // 2], fill=color)
    buf = io.BytesIO()
    save_kwargs = {}
    if fmt.upper() in ("JPEG", "JPG"):
        save_kwargs["quality"] = 90
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


def main() -> int:
    print("=" * 80)
    print("图片压缩与上传自检")
    print("=" * 80)

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CUOTIBEN_DATABASE_URL"] = (
            f"sqlite:///{(Path(tmp) / 'img.db').as_posix()}"
        )

        from app.services import image_service as svc

        created: list[Path] = []

        # ---------- 1. HEIC 支持 ----------
        print("\n-- 依赖 --")
        check("pillow-heif 已注册（HEIF 打开器可用）", svc.HEIF_AVAILABLE,
              f"HEIF_AVAILABLE={svc.HEIF_AVAILABLE}")
        import pillow_heif

        info = pillow_heif.libheif_info()
        check("libheif 具备 HEVC 解码器", bool(info.get("decoders")),
              f"{list((info.get('decoders') or {}).keys())}")
        check("libheif 具备 HEVC 编码器", bool(info.get("encoders")),
              f"{list((info.get('encoders') or {}).keys())}")

        from PIL import Image

        # 注册后 Image.open 能认 HEIC：直接验证 convert('RGB') 可用
        with Image.open(io.BytesIO(make_image(1200, 900, "JPEG"))) as im:
            check("convert('RGB') 可用", im.convert("RGB").mode == "RGB")

        # ---------- 2. 各格式压缩 ----------
        print("\n-- 压缩管线（需求 2.7 / 5.6）--")
        for label, fmt, w, h in (
            ("JPEG 4032x3024（手机横拍）", "JPEG", 4032, 3024),
            ("PNG 800x600（小于目标宽）", "PNG", 800, 600),
            ("WebP 2400x1600", "WEBP", 2400, 1600),
            ("JPEG 1080x1080（正好目标宽）", "JPEG", 1080, 1080),
            ("JPEG 3000x6000（超长竖图）", "JPEG", 3000, 6000),
        ):
            raw = make_image(w, h, fmt)
            r = svc.compress_uniform(raw)
            if r.fell_back:
                check(f"{label} 压缩", False, f"意外回退: {r.fallback_reason}")
                continue
            exp_w = 1080
            exp_h = max(1, round(h * (1080 / w))) if w != 1080 else h
            ok_size = r.width == exp_w and abs(r.height - exp_h) <= 1
            ok_bytes = r.size <= svc.MAX_OUTPUT_BYTES
            check(f"{label} -> {r.width}x{r.height} {r.size / 1024:.0f}KB",
                  ok_size and ok_bytes and r.ext == ".jpg",
                  f"期望 {exp_w}x{exp_h}，上限 {svc.MAX_OUTPUT_BYTES / 1024:.0f}KB")

        # 小图不放大
        raw_small = make_image(600, 400, "JPEG")
        r_small = svc.compress_uniform(raw_small)
        check("宽度小于 1080 的图被放大到 1080（统一尺寸）",
              r_small.width == 1080, f"{r_small.width}x{r_small.height}")

        # 输出确实是 JPEG
        with Image.open(io.BytesIO(svc.compress_uniform(make_image(2000, 1500)).data)) as out:
            check("输出格式为 JPEG", out.format == "JPEG", f"format={out.format}")

        # ---------- 3. 300KB 收紧（降质量分支）----------
        print("\n-- 300KB 上限与降质量 --")
        # 用"低阈值"精确命中 q65 分支，证明降质量真的生效。
        # 实测该输入（1080x1080，15% 噪点密度）：q75=385KB / q65=302KB / q55=245KB，
        # 阈值取 320KB -> q75 超标，必须降到 q65 才达标。
        # 不能拿均匀随机噪声测：它是 JPEG 最坏情况（q75=655KB、q55=471KB，
        # 要到 q30 才进 300KB），那种图本就无法在 q55 内达标，只能得到"回退"结论。
        speckled = make_speckled(1080, 1080, density=0.15, seed=11)
        speckled_png = _to_png(speckled)
        q75_size = _encode_size(speckled, 75)
        threshold = 320 * 1024
        r_tight = svc.compress_uniform(speckled_png, max_bytes=threshold)
        check(f"降质量分支生效（q75={q75_size / 1024:.0f}KB > 阈值 "
              f"{threshold / 1024:.0f}KB > q65）",
              (not r_tight.fell_back) and r_tight.size <= threshold,
              f"结果 {r_tight.size / 1024:.0f}KB ext={r_tight.ext} "
              f"fell_back={r_tight.fell_back}")

        # 常规真实照片量级：不同细节量都应自然落在 300KB 内。
        # 用 amp=25（轻度颗粒）模拟真实照片；amp=90 的强颗粒属于罕见场景，
        # 那种图 q75 会到 1.7~2.7MB，本就需要靠降质量/回退兜底。
        sizes = []
        for density, seed in ((0.02, 20), (0.1, 100), (0.3, 300), (0.6, 600)):
            img = make_speckled(1080, 1080, density=density, seed=seed, amp=25)
            b = svc.compress_uniform(_to_png(img))
            sizes.append((density, b.size, b.fell_back))
        worst = max(s for _, s, _ in sizes)
        any_fallback = any(f for _, _, f in sizes)
        check("常规细节量图片均在 300KB 内且不触发回退",
              worst <= svc.MAX_OUTPUT_BYTES and not any_fallback,
              "各噪点密度结果: " + ", ".join(f"{d}:{s / 1024:.0f}KB" for d, s, _ in sizes))

        # 极端不可压缩图（均匀随机噪声）：q55 仍超标 -> 回退原图，且必须如实上报
        random.seed(7)
        noise = Image.new("RGB", (1080, 1080))
        noise.putdata([(random.randint(0, 255), random.randint(0, 255),
                        random.randint(0, 255)) for _ in range(1080 * 1080)])
        noise_buf = io.BytesIO()
        noise.save(noise_buf, format="JPEG", quality=90)
        noise_jpeg = noise_buf.getvalue()
        r_noise = svc.compress_uniform(noise_jpeg)
        check("极端不可压缩图：降质量后仍超标则回退原图（不静默超限）",
              r_noise.fell_back and r_noise.data == noise_jpeg,
              f"fell_back={r_noise.fell_back} 原图 {len(noise_jpeg) / 1024:.0f}KB "
              f"reason={str(r_noise.fallback_reason)[:44]}")

        # ---------- 4. 回退原图 ----------
        print("\n-- 压缩失败回退原图（需求 2.7）--")
        broken = b"this is definitely not an image"
        r_bad = svc.compress_uniform(broken)
        check("损坏字节 -> 回退原图且不抛异常",
              r_bad.fell_back and r_bad.data == broken and r_bad.size == len(broken),
              f"fell_back={r_bad.fell_back} reason={str(r_bad.fallback_reason)[:50]}")
        check("回退时宽高为 None 而非报错", r_bad.width is None and r_bad.height is None)

        # 回退时保留原扩展名：不能把 PNG 字节写成 .jpg（文件内容会与扩展名不符）
        png_bytes = make_image(300, 200, "PNG")
        r_png = svc.compress_uniform(png_bytes, target_width=999999)
        check("正常路径输出 .jpg；回退路径保留原格式",
              r_png.ext == ".jpg" or (r_png.fell_back and r_png.ext == ".png"),
              f"ext={r_png.ext} fell_back={r_png.fell_back}")

        # ---------- 5. 存储 ----------
        print("\n-- 存储（需求 2.6）--")
        from datetime import UTC, datetime

        raw = make_image(2000, 1500, "JPEG")
        r = svc.compress_uniform(raw)
        rel, abs_path = svc.save_upload(r.data, r.ext)
        created.append(abs_path)

        check("相对路径形如 uploads/YYYY/MM/DD/uuid.jpg",
              rel.startswith("uploads/") and rel.endswith(".jpg")
              and len(rel.split("/")) == 5,
              rel)
        check("文件名是 32 位 uuid", len(Path(rel).stem) == 32, Path(rel).stem)
        check("文件已落盘", abs_path.is_file(), str(abs_path))
        check("落盘内容与压缩结果一致", abs_path.stat().st_size == r.size)

        # 两次上传不会撞名
        rel2, abs2 = svc.save_upload(r.data, r.ext)
        created.append(abs2)
        check("重复上传生成不同文件名", rel != rel2, f"{Path(rel).name} vs {Path(rel2).name}")

        # 指定日期分片
        rel3, abs3 = svc.save_upload(r.data, ".jpg", now=datetime(2026, 3, 4, tzinfo=UTC))
        created.append(abs3)
        check("按年月日分片（指定日期）", rel3.startswith("uploads/2026/03/04/"), rel3)

        # ---------- 6. 路径 <-> URL ----------
        print("\n-- 路径与 URL 互转 --")
        check("url_for 加 /uploads 前缀", svc.url_for(rel) == "/" + rel, svc.url_for(rel))
        check("url_for 幂等（已是 URL 不重复前缀）",
              svc.url_for("/" + rel) == "/" + rel, svc.url_for("/" + rel))
        check("absolute_path_of 支持三种写法",
              svc.absolute_path_of(rel) == svc.absolute_path_of("/" + rel)
              == svc.absolute_path_of(rel[len("uploads/"):]),
              rel)

        # 元数据读取
        w, h, size = svc.read_image_metadata(rel)
        check("read_image_metadata 返回真实宽高与字节数",
              w == r.width and h == r.height and size == r.size,
              f"{w}x{h} {size}B")
        check("read_image_metadata 对不存在的路径返回 None",
              svc.read_image_metadata("uploads/2099/01/01/nope.jpg") == (None, None, None))

        # ---------- 7. 校验 ----------
        print("\n-- 上传校验 --")
        from app.services.image_service import (
            ImageTooLargeError,
            UnsupportedImageError,
        )

        for ext in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"):
            try:
                svc.validate_extension("photo" + ext)
                ok = True
            except UnsupportedImageError:
                ok = False
            check(f"允许扩展名 {ext}", ok)

        for ext in (".gif", ".bmp", ".svg", ".exe", ""):
            try:
                svc.validate_extension("file" + ext)
                rejected = False
            except UnsupportedImageError:
                rejected = True
            check(f"拒绝扩展名 {ext or '(空)'}", rejected)

        try:
            svc.validate_size(b"x" * (svc.MAX_UPLOAD_BYTES + 1))
            check("超过 10MB 被拒", False)
        except ImageTooLargeError as e:
            check("超过 10MB 被拒", True, str(e)[:50])

        svc.validate_size(b"x" * 1024)
        check("1KB 通过大小校验", True)

        # ---------- 8. HTTP 端到端 ----------
        print("\n-- HTTP 端到端（TestClient）--")
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import init_db

        init_db()
        with TestClient(app) as client:
            raw = make_image(4032, 3024, "JPEG")
            resp = client.post(
                "/upload/image",
                files={"file": ("photo.jpg", raw, "image/jpeg")},
            )
            check("POST /upload/image 返回 201", resp.status_code == 201,
                  f"HTTP {resp.status_code} {resp.text[:120]}")
            if resp.status_code == 201:
                body = resp.json()
                check("响应含 url/file_path/width/height/size",
                      all(k in body for k in
                          ("url", "file_path", "width", "height", "size")),
                      str(sorted(body.keys())))
                check("width=1080", body["width"] == 1080, f"{body['width']}")
                check("height 等比（3024/4032*1080=810）", body["height"] == 810,
                      f"{body['height']}")
                check("size ≤ 300KB", body["size"] <= svc.MAX_OUTPUT_BYTES,
                      f"{body['size'] / 1024:.0f}KB")
                check("url 以 /uploads/ 开头", body["url"].startswith("/uploads/"),
                      body["url"])
                check("file_path 与 url 对应", svc.url_for(body["file_path"]) == body["url"],
                      f"{body['file_path']} -> {body['url']}")
                check("未回退（正常图应压缩成功）", body["fell_back_to_original"] is False)

                up_abs = svc.absolute_path_of(body["file_path"])
                created.append(up_abs)
                check("文件真实写入 uploads/", up_abs.is_file(), str(up_abs))

                # 静态挂载能访问
                static_resp = client.get(body["url"])
                check("静态挂载 /uploads 可访问该图片",
                      static_resp.status_code == 200 and len(static_resp.content) == body["size"],
                      f"HTTP {static_resp.status_code} {len(static_resp.content)}B")

                # 建题时能读到图片元数据（验证与 question_service 打通）
                from app.services import folder_service, question_service
                from app.database import SessionLocal

                with SessionLocal() as db:
                    subj = folder_service.create_folder(db, "高等数学")
                    cat = folder_service.create_folder(db, "极限", parent_id=subj.id)
                    q = question_service.create_question(
                        db, folder_id=cat.id, stem="带图题",
                        images=[body["file_path"]],
                    )
                    img = q.images[0]
                    check("建题时自动读到图片宽高",
                          img.width == 1080 and img.height == 810 and img.size == body["size"],
                          f"{img.width}x{img.height} {img.size}B")

            # 非法扩展名
            bad = client.post("/upload/image",
                              files={"file": ("evil.gif", b"GIF89a", "image/gif")})
            check("上传 .gif -> 400", bad.status_code == 400,
                  f"HTTP {bad.status_code}")

            # 空文件
            empty = client.post("/upload/image",
                                files={"file": ("empty.jpg", b"", "image/jpeg")})
            check("上传空文件 -> 400", empty.status_code == 400, f"HTTP {empty.status_code}")

            # 超 10MB
            big = client.post("/upload/image",
                              files={"file": ("big.jpg", b"x" * (svc.MAX_UPLOAD_BYTES + 10),
                                              "image/jpeg")})
            check("上传 >10MB -> 413", big.status_code == 413, f"HTTP {big.status_code}")

            # 损坏内容：应回退原图并成功 201（不阻断上传）
            broken_resp = client.post("/upload/image",
                                      files={"file": ("broken.jpg", b"not an image at all",
                                                      "image/jpeg")})
            check("损坏图片回退原图且仍 201（不阻断上传）",
                  broken_resp.status_code == 201
                  and broken_resp.json().get("fell_back_to_original") is True,
                  f"HTTP {broken_resp.status_code} {str(broken_resp.json())[:90]}")
            if broken_resp.status_code == 201:
                created.append(svc.absolute_path_of(broken_resp.json()["file_path"]))

        # ---------- 9. 删除 ----------
        print("\n-- 删除 --")
        target = created[0]
        rel_target = target.relative_to(ROOT).as_posix()
        check("删除前文件存在", target.is_file(), rel_target)
        check("delete_file 成功", svc.delete_file(rel_target) is True)
        check("删除后文件不存在", not target.is_file())
        check("重复删除返回 False", svc.delete_file(rel_target) is False)
        check("拒绝删除 uploads 之外的路径（防路径穿越）",
              svc.delete_file("../../etc/passwd") is False)

        from app.database import engine

        engine.dispose()

        # 清理本次测试产生的所有文件
        for p in created:
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
        # 清理空的分片目录
        if svc.UPLOADS_DIR.exists():
            for d in sorted(svc.UPLOADS_DIR.rglob("*"), reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    try:
                        d.rmdir()
                    except OSError:
                        pass

    print("-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
