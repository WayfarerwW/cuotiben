"""题目路由（requirements.md 4.2）。

只做参数校验与响应组装：取参数 → 调 service → 转成 schema 返回。
业务规则一律在 services/question_service.py（AGENTS.md 3.4）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Question
from ..schemas import (
    MasteryStatus,
    MasteryUpdate,
    MessageOut,
    QuestionCreate,
    QuestionListItem,
    QuestionOut,
    QuestionUpdate,
)
from ..services import question_service
from ..services.folder_service import (
    FolderNotFoundError,
    InvalidFolderStructureError,
)
from ..services.question_service import QuestionNotFoundError
from ..services.tag_service import InvalidTagError

router = APIRouter(tags=["questions"])


@contextmanager
def _service_errors() -> Iterator[None]:
    """把 service 业务异常统一转成 HTTP 状态码。

    service 不 import HTTPException（保持与 Web 框架解耦），转换放在这里。
    用一个上下文管理器收口，避免每个路由重复五段 try/except。
    """
    try:
        yield
    except (QuestionNotFoundError, FolderNotFoundError) as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except (InvalidFolderStructureError, InvalidTagError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except question_service.QuestionError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


# --------------------------------------------------------------------------
# 4.2 题目
# --------------------------------------------------------------------------


@router.post("/questions", response_model=QuestionOut,
             status_code=status.HTTP_201_CREATED, summary="创建题目")
def create_question(payload: QuestionCreate, db: Session = Depends(get_db)) -> Question:
    """创建题目；自动生成首条 review_record（next_review_at = now() + 3天）。"""
    with _service_errors():
        question = question_service.create_question(
            db,
            folder_id=payload.folder_id,
            stem=payload.stem,
            answer=payload.answer,
            tags=payload.tags,
            is_starred=payload.is_starred,
            mastery_status=payload.mastery_status.value,
            sort_order=payload.sort_order,
            images=payload.images,
        )
    question_service.attach_review_state(db, [question])
    return question


@router.get("/questions", response_model=list[QuestionListItem],
            summary="题目列表（多条件筛选）")
def list_questions(
    folder_id: int | None = Query(None, description="按文件夹筛选"),
    tag: list[str] | None = Query(None, description="标签，可重复传多个"),
    tag_mode: str = Query("and", pattern="^(and|or)$",
                          description="多标签匹配：and=全含，or=含任一"),
    keyword: str | None = Query(None, description="题干/答案关键词"),
    starred: bool | None = Query(None, description="只看重点"),
    mastery: MasteryStatus | None = Query(None, description="按正误状态筛选"),
    db: Session = Depends(get_db),
) -> list[Question]:
    filters = question_service.QuestionFilters(
        folder_id=folder_id,
        tags=list(tag or []),
        tag_mode=tag_mode,
        keyword=keyword,
        starred=starred,
        mastery=mastery.value if mastery else None,
    )
    questions = question_service.list_questions(db, filters)
    question_service.attach_review_state(db, questions)
    return questions


@router.get("/questions/{question_id}", response_model=QuestionOut, summary="题目详情")
def get_question(question_id: int, db: Session = Depends(get_db)) -> Question:
    with _service_errors():
        question = question_service.get_question(db, question_id)
    question_service.attach_review_state(db, [question])
    return question


@router.put("/questions/{question_id}", response_model=QuestionOut, summary="编辑题目")
def update_question(
    question_id: int, payload: QuestionUpdate, db: Session = Depends(get_db)
) -> Question:
    with _service_errors():
        question = question_service.update_question(
            db,
            question_id,
            folder_id=payload.folder_id,
            stem=payload.stem,
            answer=payload.answer,
            tags=payload.tags,
            is_starred=payload.is_starred,
            mastery_status=payload.mastery_status.value
            if payload.mastery_status
            else None,
            sort_order=payload.sort_order,
            images=payload.images,
        )
    question_service.attach_review_state(db, [question])
    return question


@router.delete("/questions/{question_id}", response_model=MessageOut, summary="软删除题目")
def delete_question(question_id: int, db: Session = Depends(get_db)) -> MessageOut:
    with _service_errors():
        question_service.delete_question(db, question_id)
    return MessageOut(message=f"已软删除题目 {question_id}")


@router.post("/questions/{question_id}/star", response_model=QuestionOut,
             summary="标记为重点")
def star_question(question_id: int, db: Session = Depends(get_db)) -> Question:
    with _service_errors():
        question = question_service.set_starred(db, question_id, True)
    question_service.attach_review_state(db, [question])
    return question


@router.post("/questions/{question_id}/unstar", response_model=QuestionOut,
             summary="取消重点")
def unstar_question(question_id: int, db: Session = Depends(get_db)) -> Question:
    with _service_errors():
        question = question_service.set_starred(db, question_id, False)
    question_service.attach_review_state(db, [question])
    return question


@router.post("/questions/{question_id}/mastery", response_model=QuestionOut,
             summary="切换/设置正误状态")
def set_mastery(
    question_id: int,
    payload: MasteryUpdate | None = None,
    db: Session = Depends(get_db),
) -> Question:
    """不传 mastery_status 时在两个状态之间翻转。"""
    with _service_errors():
        if payload is not None and payload.mastery_status is not None:
            question = question_service.set_mastery(
                db, question_id, payload.mastery_status.value
            )
        else:
            question = question_service.toggle_mastery(db, question_id)
    question_service.attach_review_state(db, [question])
    return question
