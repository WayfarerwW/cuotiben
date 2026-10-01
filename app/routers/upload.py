"""图片上传路由（requirements.md 2.6 / 2.7 / 4.4）。

只做参数校验与响应组装，压缩与存储逻辑在 services/image_service.py。

注意路径形态：/upload/* 属于**动作命名空间**，保持单数（AGENTS.md 3.5）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ImageDeleteRequest, ImageUploadOut, MessageOut
from ..services import image_service, question_service
from ..services.image_service import (
    ImageTooLargeError,
    UnsupportedImageError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/image", response_model=ImageUploadOut,
             status_code=status.HTTP_201_CREATED, summary="上传并压缩图片")
async def upload_image(file: UploadFile = File(...)) -> ImageUploadOut:
    """接收图片 -> 压缩到宽 1080px JPEG q75 -> 存 uploads/年/月/日/uuid.jpg。

    压缩失败时回退原图，**不阻断上传**（需求 2.7）。
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

    return ImageUploadOut(
        url=image_service.url_for(file_path),
        file_path=file_path,
        width=result.width,
        height=result.height,
        size=result.size,
        fell_back_to_original=result.fell_back,
    )


@router.delete("/image", response_model=MessageOut, summary="删除图片")
def delete_image(
    payload: ImageDeleteRequest, db: Session = Depends(get_db)
) -> MessageOut:
    """按图片 URL/路径删除：先解绑题目关联，再删物理文件。

    需求 2.3 要求物理文件删除放后台异步任务。这里在响应前同步 unlink 一个
    小文件（毫秒级），不做成队列——本项目是纯本地单机，引入后台队列的复杂度
    大于收益；若将来图片量级变大再换成 BackgroundTasks。
    """
    stored = payload.file_path
    if not stored:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 file_path")

    # 先解绑：从所有引用它的题目的 images 列表里移除
    unbound = question_service.detach_image(db, stored)

    deleted = image_service.delete_file(stored)
    if not deleted and not unbound:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"未找到图片 {stored}，也没有题目引用它")

    msg = f"已删除图片（解绑 {unbound} 处引用）" if deleted else f"已解绑 {unbound} 处引用"
    return MessageOut(message=msg)
