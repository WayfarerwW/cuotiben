"""记事本业务逻辑（requirements.md 2.15 / 4.6）。

要点：
  - 独立入口，标题与内容都可空
  - 列表按 updated_at 倒序（最近编辑的在前）
  - 关键词搜索：标题 **和** 内容模糊匹配
  - 删除是软删除（deleted_at）
  - question_id 可空，用于关联题目（需求标为"进阶"能力）
"""

from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from ..models import Note, Question
from ..models.base import utcnow

# 列表项里 content 的截断长度。
# 列表契约要求返回 content，但一篇很长的笔记（比如整页 Markdown）会把整个
# 列表响应撑得很大；列表通常只用于预览，所以超过这个长度就截断并加省略号。
# 详情接口始终返回完整内容。
MAX_LIST_CONTENT = 200


class NoteError(Exception):
    """记事本相关业务异常基类。"""


class NoteNotFoundError(NoteError):
    """笔记不存在（或已软删除）。"""


class NoteQuestionNotFoundError(NoteError):
    """要关联的题目不存在。"""


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------


def _base_query() -> Select:
    return select(Note).where(Note.deleted_at.is_(None))


def list_notes(db: Session) -> list[Note]:
    """全部笔记，按 updated_at 倒序（requirements.md 2.15）。"""
    return list(
        db.scalars(
            _base_query().order_by(Note.updated_at.desc(), Note.id.desc())
        ).all()
    )


def get_note(db: Session, note_id: int) -> Note:
    """按 id 取未删除笔记。"""
    note = db.scalars(_base_query().where(Note.id == note_id)).one_or_none()
    if note is None:
        raise NoteNotFoundError(f"笔记 {note_id} 不存在")
    return note


def search_notes(db: Session, keyword: str, limit: int = 100) -> list[Note]:
    """关键词搜索标题与内容，按 updated_at 倒序（requirements.md 4.6）。

    关键词为空时返回空列表（而不是全量），避免前端误把"空搜索"当成"列全部"。
    """
    text = (keyword or "").strip()
    if not text:
        return []
    like = f"%{text}%"
    stmt = (
        _base_query()
        .where(or_(Note.title.like(like), Note.content.like(like)))
        .order_by(Note.updated_at.desc(), Note.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def truncate_for_list(content: str | None) -> str | None:
    """列表展示用的内容截断（详情不受影响）。"""
    if content is None or len(content) <= MAX_LIST_CONTENT:
        return content
    return content[:MAX_LIST_CONTENT] + "…"


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------


def _validate_question(db: Session, question_id: int | None) -> None:
    """校验关联题目存在且未删除；None 表示不关联，直接通过。"""
    if question_id is None:
        return
    exists = db.scalars(
        select(Question.id).where(
            Question.id == question_id, Question.deleted_at.is_(None)
        )
    ).first()
    if exists is None:
        raise NoteQuestionNotFoundError(f"题目 {question_id} 不存在")


def create_note(
    db: Session,
    *,
    title: str | None = None,
    content: str | None = None,
    question_id: int | None = None,
) -> Note:
    """新建笔记。标题与内容都可空（允许先建空笔记再写）。"""
    _validate_question(db, question_id)
    note = Note(title=title, content=content, question_id=question_id)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def update_note(
    db: Session,
    note_id: int,
    *,
    title: str | None = None,
    content: str | None = None,
    question_id: int | None = None,
    fields_to_update: set[str] | None = None,
) -> Note:
    """编辑笔记。

    `fields_to_update` 用于区分"没传这个字段"与"传了 null/空串"：
      - 没传 -> 不动
      - 传了 null 或 "" -> 清空该字段
    不传该参数时退化为"非 None 即更新"（旧调用方式）。

    updated_at 由 ORM 的 onupdate 自动刷新。
    """
    note = get_note(db, note_id)
    explicit = fields_to_update is not None

    if (explicit and "title" in fields_to_update) or (not explicit and title is not None):
        note.title = title
    if (explicit and "content" in fields_to_update) or (not explicit and content is not None):
        note.content = content
    if explicit and "question_id" in fields_to_update:
        _validate_question(db, question_id)
        note.question_id = question_id
    elif not explicit and question_id is not None:
        _validate_question(db, question_id)
        note.question_id = question_id

    db.commit()
    db.refresh(note)
    return note


def delete_note(db: Session, note_id: int) -> None:
    """软删除笔记（requirements.md 2.15）。"""
    note = get_note(db, note_id)
    note.deleted_at = utcnow()
    db.commit()
