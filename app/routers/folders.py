"""文件夹路由（requirements.md 4.1）。

只做参数校验与响应组装：取参数 → 调 service → 转成 schema 返回。
业务规则一律在 services/folder_service.py（AGENTS.md 3.4）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Folder
from ..schemas import (
    FolderCreate,
    FolderDeleteResult,
    FolderOut,
    FolderTree,
    FolderUpdate,
)
from ..services import folder_service
from ..services.folder_service import (
    DuplicateFolderNameError,
    FolderNotFoundError,
    InvalidFolderStructureError,
)

router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("/tree", response_model=list[FolderTree], summary="完整文件夹树")
def get_tree(db: Session = Depends(get_db)) -> list[Folder]:
    """返回两级树：顶层是学科，children 是大类。

    直接返回 ORM 对象，由 response_model 负责序列化（FolderTree 带 from_attributes）。
    """
    return folder_service.list_tree(db)


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED,
             summary="创建学科或大类")
def create_folder(payload: FolderCreate, db: Session = Depends(get_db)) -> FolderOut:
    try:
        folder = folder_service.create_folder(
            db, payload.name, payload.parent_id, payload.sort_order
        )
    except FolderNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except DuplicateFolderNameError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except InvalidFolderStructureError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return FolderOut.model_validate(folder)


@router.put("/{folder_id}", response_model=FolderOut, summary="重命名 / 调整排序")
def update_folder(
    folder_id: int, payload: FolderUpdate, db: Session = Depends(get_db)
) -> FolderOut:
    try:
        folder = folder_service.update_folder(
            db, folder_id, name=payload.name, sort_order=payload.sort_order
        )
    except FolderNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except DuplicateFolderNameError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except InvalidFolderStructureError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return FolderOut.model_validate(folder)


@router.delete("/{folder_id}", response_model=FolderDeleteResult,
               summary="软删除文件夹（连同其下题目）")
def delete_folder(
    folder_id: int,
    force: bool = Query(
        False,
        description="文件夹下仍有题目时，是否连同这些题目一起删除"
        "（false 则拒绝并返回 409）"),
    db: Session = Depends(get_db),
) -> FolderDeleteResult:
    try:
        info = folder_service.delete_folder(db, folder_id, force=force)
    except FolderNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except InvalidFolderStructureError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    # 把"删了什么、有多少图片变成孤儿"讲清楚：用户需要知道去清理图片
    message = f"已删除 {info['folders']} 个文件夹"
    if info["questions"]:
        message += f"、{info['questions']} 道题目"
    if info["images"]:
        message += (f"；{info['images']} 张图片已无人引用，"
                    "可在「数据说明页 → 清理孤儿图片」回收")
    return FolderDeleteResult(
        message=message,
        folders=info["folders"],
        questions=info["questions"],
        images=info["images"],
    )
