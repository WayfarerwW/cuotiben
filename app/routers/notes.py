"""记事本路由（requirements.md 4.6）。

只做参数校验与响应组装，业务逻辑在 services/note_service.py。
路径形态：/notes 是资源集合，用复数（AGENTS.md 3.5）。

注意路由注册顺序：`/notes/search` 必须写在 `/notes/{note_id}` **之前**。
否则 "search" 会被当作 `{note_id}` 的值去解析成整数，返回 422。
（实现中 note_id 声明为 int，FastAPI 会因类型不匹配而拒绝，不会静默走错分支，
但提前注册语义更清楚、也避免以后误改成 str。）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Note
from ..schemas import MessageOut, NoteCreate, NoteListItem, NoteOut, NoteUpdate
from ..services import note_service
from ..services.note_service import NoteNotFoundError, NoteQuestionNotFoundError

router = APIRouter(prefix="/notes", tags=["notes"])


def _to_list_item(note) -> NoteListItem:
    """列表项：对超长 content 做截断（详情不截断）。"""
    return NoteListItem(
        id=note.id,
        title=note.title,
        content=note_service.truncate_for_list(note.content),
        question_id=note.question_id,
        updated_at=note.updated_at,
    )


@router.get("/search", response_model=list[NoteListItem], summary="搜索笔记")
def search_notes(
    q: str = Query("", description="关键词，匹配标题与内容"),
    limit: int = Query(100, ge=1, le=500, description="最多返回条数"),
    db: Session = Depends(get_db),
) -> list[NoteListItem]:
    """标题 **和** 内容模糊匹配，按 updated_at 倒序。

    q 为空时返回空列表（不返回全部），避免前端把"空搜索"误当"列全部"。
    """
    return [_to_list_item(n) for n in note_service.search_notes(db, q, limit=limit)]


@router.get("", response_model=list[NoteListItem], summary="笔记列表")
def list_notes(db: Session = Depends(get_db)) -> list[NoteListItem]:
    """全部笔记，按 updated_at 倒序（最近编辑的在前）。"""
    return [_to_list_item(n) for n in note_service.list_notes(db)]


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED,
             summary="新建笔记")
def create_note(payload: NoteCreate, db: Session = Depends(get_db)) -> Note:
    """标题与内容都可空。"""
    try:
        return note_service.create_note(
            db,
            title=payload.title,
            content=payload.content,
            question_id=payload.question_id,
        )
    except NoteQuestionNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.get("/{note_id}", response_model=NoteOut, summary="笔记详情")
def get_note(note_id: int, db: Session = Depends(get_db)) -> Note:
    """返回完整内容，不做列表那样的截断。"""
    try:
        return note_service.get_note(db, note_id)
    except NoteNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.put("/{note_id}", response_model=NoteOut, summary="编辑笔记")
def update_note(
    note_id: int, payload: NoteUpdate, db: Session = Depends(get_db)
) -> Note:
    """只更新请求体里出现的字段。

    传 `null` 或空串表示清空该字段；未出现的字段保持原值 ——
    这样"只改标题"和"清空内容"两种意图都能表达。
    """
    try:
        return note_service.update_note(
            db,
            note_id,
            title=payload.title,
            content=payload.content,
            question_id=payload.question_id,
            fields_to_update=set(payload.model_fields_set),
        )
    except NoteNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except NoteQuestionNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.delete("/{note_id}", response_model=MessageOut, summary="删除笔记（软删除）")
def delete_note(note_id: int, db: Session = Depends(get_db)) -> MessageOut:
    try:
        note_service.delete_note(db, note_id)
    except NoteNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return MessageOut(message=f"已软删除笔记 {note_id}")
