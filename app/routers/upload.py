"""图片上传路由（requirements.md 2.6 / 2.7 / 4.4）。

只做参数校验与响应组装，压缩与存储逻辑在 services/image_service.py。

注意路径形态：/upload/* 属于**动作命名空间**，保持单数（AGENTS.md 3.5）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ImageDeleteResult, ImageUploadOut
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
