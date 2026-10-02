"""图片上传路由（requirements.md 2.6 / 2.7 / 4.4）。

只做参数校验与响应组装，压缩与存储逻辑在 services/image_service.py。

注意路径形态：/upload/* 属于**动作命名空间**，保持单数（AGENTS.md 3.5）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import (
    ImageDeleteResult,
    ImageUploadOut,
    OrphanCleanupOut,
    OrphanImageOut,
    OrphanListOut,
)
from ..services import image_service, question_service
from ..services.image_service import (
    ImageTooLargeError,
    UnsupportedImageError,
)
from ..services.question_service import QuestionImageNotFoundError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/image", response_model=ImageUploadOut,
             status_code=status.HTTP_201_CREATED, summary="上传并压缩图片")
async def upload_image(
    file: UploadFile = File(...), db: Session = Depends(get_db)
) -> ImageUploadOut:
    """接收图片 -> 压缩到宽 1080px JPEG q75 -> 存 uploads/年/月/日/uuid.jpg。

    压缩失败时回退原图，**不阻断上传**（需求 2.7）。

    同时落一条 question_images 记录并返回其主键 id，这样：
      - 前端录题时把 url 放进 questions.images（会自动复用同一条记录）
      - 删除时可用 DELETE /upload/image/{id}
    """
    try:
        image_service.validate_extension(file.filename)
    except UnsupportedImageError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "上传内容为空")

    try:
        image_service.validate_size(data)
    except ImageTooLargeError as e:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(e)) from e

    result = image_service.compress_uniform(data)
    file_path, _absolute = image_service.save_upload(result.data, result.ext)

    record = question_service.register_image(
        db, file_path=file_path, width=result.width, height=result.height,
        size=result.size,
    )

    return ImageUploadOut(
        id=record.id,
        url=image_service.url_for(file_path),
        file_path=file_path,
        width=result.width,
        height=result.height,
        size=result.size,
        fell_back_to_original=result.fell_back,
    )


@router.delete("/image/{image_id}", response_model=ImageDeleteResult,
               summary="删除图片")
def delete_image(image_id: int, db: Session = Depends(get_db)) -> ImageDeleteResult:
    """按 question_images 主键删除图片（requirements.md 4.4）。

    会同时解绑引用它的题目关联并删除物理文件；
    需求 2.3 说物理删除放后台异步任务，这里在响应前同步 unlink 一个小文件
    （毫秒级）—— 纯本地单机引入后台队列的复杂度大于收益，量级变大再换。
    """
    try:
        info = question_service.delete_image(db, image_id)
    except QuestionImageNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return ImageDeleteResult(**info)


# --------------------------------------------------------------------------
# 孤儿图片清理
# --------------------------------------------------------------------------

_ORPHAN_NOTE = (
    "孤儿图片 = uploads/ 下存在、但没有任何**未删除**题目在引用的文件。"
    "包括：上传后没保存就关掉弹窗的、上传后从未拿去建题的、"
    "以及挂在已删除题目上的（题目已不可见，图片不会再被渲染）。"
    "被未删除题目引用的图片不会列出，清理也不会动它们。"
)


@router.get("/orphans", response_model=OrphanListOut, summary="列出孤儿图片")
def list_orphans(db: Session = Depends(get_db)) -> OrphanListOut:
    """列出 uploads/ 下没有被任何未删除题目引用的图片。**只读，不删东西。**"""
    from ..services.image_service import scan_orphans

    # 扫描要遍历 uploads/ 并查库，放线程池避免阻塞事件循环
    orphans = scan_orphans(db)
    return OrphanListOut(
        count=len(orphans),
        total_bytes=sum(o.size for o in orphans),
        files=[OrphanImageOut(**vars(o)) for o in orphans],
        note=_ORPHAN_NOTE,
    )


@router.post("/cleanup", response_model=OrphanCleanupOut, summary="清理孤儿图片")
async def cleanup_orphans(db: Session = Depends(get_db)) -> OrphanCleanupOut:
    """删除所有孤儿图片的物理文件，返回清理数量。

    放进线程池：要遍历目录并逐个 unlink，直接在 async 里做会阻塞事件循环。

    安全性：删除走 `image_service.delete_file`，它内部会把路径解析成绝对路径、
    校验**必须仍在 uploads/ 之内**（防目录穿越）之后才 unlink。
    本接口的扫描阶段也做了同一层校验。
    """
    from ..services.image_service import cleanup_orphans as _cleanup

    result = await run_in_threadpool(_cleanup, db)
    return OrphanCleanupOut(**result)
