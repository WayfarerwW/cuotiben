"""图片处理与存储（requirements.md 2.6 / 2.7 / 5.6，AGENTS.md 4.4）。

要点：
  - **必须显式 register_heif_opener()**，否则 HEIC 打不开（AGENTS.md 4.4）
  - 统一输出宽度 1080px、等比不裁剪、JPEG quality=75、optimize=True
  - 单图目标 ≤ 300KB：直接按 q75 存完若超标，逐级降质量重试
  - **压缩失败回退原图，不阻断上传**（需求 2.7）
  - 目录按年月日分片、文件名 UUID；数据库只存相对路径
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps

# HEIC 支持：必须在任何 Image.open 之前注册（AGENTS.md 4.4）
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失时降级，不影响其他格式
    HEIF_AVAILABLE = False

logger = logging.getLogger(__name__)

# 项目根：app/services/image_service.py -> app/services -> app -> 根
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
UPLOADS_DIR = PROJECT_ROOT / "uploads"
# 静态挂载前缀，必须与 main.py 的 app.mount 一致
UPLOADS_URL_PREFIX = "/uploads"

# ---- 需求 2.6 / 2.7 参数表 ----
TARGET_WIDTH = 1080
JPEG_QUALITY = 75
MAX_OUTPUT_BYTES = 300 * 1024        # 单图 ≤ 300KB
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 单文件上限 10MB
# 超标时的降质量梯度（在 q75 不达标时依次尝试）
FALLBACK_QUALITIES = (65, 55)
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


class ImageError(Exception):
    """图片处理相关业务异常。"""


class ImageTooLargeError(ImageError):
    """上传文件超过大小上限。"""


class UnsupportedImageError(ImageError):
    """扩展名不在允许列表内。"""


@dataclass
class CompressedImage:
    """压缩结果。

    fell_back=True 表示走的是"回退原图"路径：data 是原始字节，
    ext 是原始扩展名（此时不是 JPEG），失败原因在 fallback_reason。
    """

    data: bytes
    width: int | None
    height: int | None
    ext: str
    fell_back: bool = False
    fallback_reason: str | None = None

    @property
    def size(self) -> int:
        return len(self.data)


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def validate_extension(filename: str | None) -> str:
    """校验扩展名并返回小写形式（含点）。"""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedImageError(
            f"不支持的图片格式「{ext or '未知'}」，"
            f"仅支持 {'/'.join(sorted(e.lstrip('.') for e in ALLOWED_EXTENSIONS))}"
        )
    return ext


def validate_size(data: bytes) -> None:
    """校验上传字节数是否超限。"""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ImageTooLargeError(
            f"图片过大：{len(data) / 1024 / 1024:.1f}MB，上限 "
            f"{MAX_UPLOAD_BYTES / 1024 / 1024:.0f}MB"
        )


# --------------------------------------------------------------------------
# 压缩（需求 5.6 的实现，含 300KB 收紧与失败回退）
# --------------------------------------------------------------------------


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def compress_uniform(
    file_bytes: bytes,
    target_width: int = TARGET_WIDTH,
    quality: int = JPEG_QUALITY,
    max_bytes: int = MAX_OUTPUT_BYTES,
) -> CompressedImage:
    """统一宽高比压缩为 JPEG。

    流程（需求 5.6）：
      1. 打开并 convert("RGB")
      2. 宽度缩到 target_width，高度等比（不变形、不裁剪）
      3. 存 JPEG quality=75 optimize=True
      4. 若超过 max_bytes，依次降质量重试（65、55）
      5. 任一步失败 / 仍然超标 -> 回退原图，标记 fell_back

    回退时绝不抛异常：需求 2.7 要求压缩失败不阻断上传。
    """
    original_size: tuple[int, int] | None = None
    try:
        with Image.open(io.BytesIO(file_bytes)) as src:
            # 手机竖拍照片常带 EXIF 方向，先按方向摆正，否则"等比缩放"后
            # 看到的仍是躺倒的图（尺寸算对了但视觉是错的）
            oriented = ImageOps.exif_transpose(src) or src
            original_size = oriented.size
            img = oriented.convert("RGB")

            w, h = img.size
            if w != target_width:
                if w <= 0:
                    raise ValueError("图片宽度为 0")
                ratio = target_width / w
                # 至少 1px，避免极端长图算出 0 高
                img = img.resize((target_width, max(1, int(h * ratio))), Image.LANCZOS)

            data = _encode_jpeg(img, quality)
            final_size = img.size

        if len(data) <= max_bytes:
            return CompressedImage(data=data, width=final_size[0],
                                   height=final_size[1], ext=".jpg")

        # 超标：逐级降质量
        for q in FALLBACK_QUALITIES:
            if q >= quality:
                continue
            with Image.open(io.BytesIO(file_bytes)) as src2:
                img2 = (ImageOps.exif_transpose(src2) or src2).convert("RGB")
                w2, h2 = img2.size
                if w2 != target_width:
                    ratio = target_width / w2
                    img2 = img2.resize((target_width, max(1, int(h2 * ratio))),
                                       Image.LANCZOS)
                data = _encode_jpeg(img2, q)
                final_size = img2.size
            if len(data) <= max_bytes:
                logger.info("图片压缩：q%d 后达标 %d 字节", q, len(data))
                return CompressedImage(data=data, width=final_size[0],
                                       height=final_size[1], ext=".jpg")

        reason = f"压缩后仍为 {len(data) / 1024:.0f}KB，超过 {max_bytes / 1024:.0f}KB 上限"
    except Exception as e:  # noqa: BLE001  压缩失败一律回退，不阻断上传
        reason = f"{type(e).__name__}: {e}"

    logger.warning("图片压缩失败，回退原图：%s", reason)
    return CompressedImage(
        data=file_bytes,
        width=original_size[0] if original_size else None,
        height=original_size[1] if original_size else None,
        # 回退时保持原格式，避免把 PNG/HEIC 字节硬写成 .jpg 导致文件损坏
        ext=_guess_original_ext(file_bytes),
        fell_back=True,
        fallback_reason=reason,
    )


def _guess_original_ext(data: bytes) -> str:
    """按内容嗅探原始格式，猜不到就用 .bin（保证文件内容与扩展名一致）。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").lower()
        mapping = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png",
                   "webp": ".webp", "heif": ".heic", "heic": ".heic"}
        return mapping.get(fmt, ".bin")
    except Exception:  # noqa: BLE001
        return ".bin"


# --------------------------------------------------------------------------
# 存储（需求 2.6：按年月日分片、UUID 文件名、库内存相对路径）
# --------------------------------------------------------------------------


def storage_dir(now: datetime | None = None) -> Path:
    """当日分片目录 uploads/YYYY/MM/DD。"""
    moment = now or datetime.now(UTC)
    return UPLOADS_DIR / f"{moment:%Y}" / f"{moment:%m}" / f"{moment:%d}"


def save_upload(
    data: bytes, ext: str, now: datetime | None = None
) -> tuple[str, Path]:
    """把字节写入分片目录，返回 (相对路径, 绝对路径)。

    相对路径形如 uploads/2026/10/01/{uuid}.jpg，与需求 2.6 一致，
    也是写进 question_images.file_path 的值。
    """
    directory = storage_dir(now)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    absolute = directory / filename
    absolute.write_bytes(data)
    relative = absolute.relative_to(PROJECT_ROOT).as_posix()
    return relative, absolute


# --------------------------------------------------------------------------
# 路径 <-> URL 互转
# --------------------------------------------------------------------------


def url_for(relative_path: str) -> str:
    """库内相对路径 -> 前端可访问的 URL。

    存 "uploads/2026/10/01/x.jpg"，返回 "/uploads/2026/10/01/x.jpg"。
    只返回路径不带域名：端口/主机可能变（局域网访问），存绝对 URL 会失效。
    """
    normalized = relative_path.replace("\\", "/").lstrip("/")
    prefix = UPLOADS_URL_PREFIX.lstrip("/")
    if normalized.startswith(prefix + "/"):
        normalized = normalized[len(prefix) + 1:]
    elif normalized == prefix:
        normalized = ""
    return f"{UPLOADS_URL_PREFIX}/{normalized}" if normalized else UPLOADS_URL_PREFIX


def absolute_path_of(stored_path: str) -> Path:
    """把库内路径或 URL 统一解析成磁盘绝对路径。

    接受三种写法：
        uploads/2026/10/01/x.jpg
        /uploads/2026/10/01/x.jpg
        2026/10/01/x.jpg
    """
    text = (stored_path or "").replace("\\", "/").strip()
    prefix = UPLOADS_URL_PREFIX.strip("/")
    if text.startswith("/"):
        text = text.lstrip("/")
    if text.startswith(prefix + "/"):
        text = text[len(prefix) + 1:]
    elif text == prefix:
        text = ""
    return UPLOADS_DIR / text


def read_image_metadata(stored_path: str) -> tuple[int | None, int | None, int | None]:
    """读取已存图片的宽高与字节数；读不到返回 (None, None, None)。

    供 question_service 建题时填充 question_images 的元数据字段。
    """
    try:
        path = absolute_path_of(stored_path)
        if not path.is_file():
            return None, None, None
        size = path.stat().st_size
        with Image.open(path) as img:
            width, height = img.size
        return width, height, size
    except Exception:  # noqa: BLE001  元数据只是锦上添花，失败不该阻断建题
        return None, None, None


def delete_file(stored_path: str) -> bool:
    """删除磁盘文件，返回是否真的删掉了。

    需求 2.3：图片物理文件删除放后台异步任务。本函数只负责"删"这一个动作，
    调用方决定何时在后台执行（目前由 routers 在响应之后触发）。
    """
    try:
        path = absolute_path_of(stored_path)
        # 只允许删 uploads/ 下的文件，避免路径穿越删到项目其他文件
        if not path.resolve().is_relative_to(UPLOADS_DIR.resolve()):
            logger.warning("拒绝删除 uploads 之外的路径：%s", stored_path)
            return False
        if path.is_file():
            path.unlink()
            return True
        return False
    except Exception:  # noqa: BLE001
        logger.warning("删除图片文件失败：%s", stored_path, exc_info=True)
        return False
