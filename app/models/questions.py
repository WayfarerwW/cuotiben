"""questions —— 题目。requirements.md 3.2 / 2.3。

stem 与 answer 都允许为空（纯图片题目）；is_starred 与 mastery_status
是两个独立维度，可自由组合（requirements.md 2.14）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    CreatedAtMixin,
    PKMixin,
    SoftDeleteMixin,
    UpdatedAtMixin,
    fk_cascade,
)
from .question_tags import question_tags

if TYPE_CHECKING:
    from .folders import Folder
    from .notes import Note
    from .question_images import QuestionImage
    from .review_records import ReviewRecord
    from .tags import Tag

# mastery_status 取值（requirements.md 2.14）
MASTERY_STILL_WRONG = "still_wrong"
MASTERY_MASTERED = "mastered"


class Question(PKMixin, CreatedAtMixin, UpdatedAtMixin, SoftDeleteMixin, Base):
    __tablename__ = "questions"

    folder_id: Mapped[int] = fk_cascade("folders.id")
    stem: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    mastery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MASTERY_STILL_WRONG
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    folder: Mapped["Folder"] = relationship(back_populates="questions")

    # 子表都是真实存在的行，删题目时应物理级联删除（DB 层 ON DELETE CASCADE）。
    # 这里同步声明 ORM 级联，保证 session.delete(question) 也清理干净。
    images: Mapped[list["QuestionImage"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionImage.sort_order, QuestionImage.id",
    )
    review_records: Mapped[list["ReviewRecord"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
    )
    tags: Mapped[list["Tag"]] = relationship(
        secondary=question_tags,
        back_populates="questions",
    )
    # notes.question_id 可空；硬删题目时由 DB 层处理（SET NULL），
    # 不在这里做 ORM 级联，避免影响 notes 表本身。
    notes: Mapped[list["Note"]] = relationship(
        back_populates="question",
        passive_deletes=True,
    )

    __table_args__ = (
        # 列表页按文件夹 + 重点 + 排序取数的常见路径
        Index("ix_questions_folder_starred", "folder_id", "is_starred", "sort_order"),
        Index("ix_questions_mastery", "mastery_status"),
        Index("ix_questions_deleted", "deleted_at"),
    )
