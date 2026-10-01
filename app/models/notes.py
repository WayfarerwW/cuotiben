"""notes —— 记事本。requirements.md 3.7 / 2.15。

列表按 updated_at 倒序；支持关键词搜索（应用层实现）。
question_id 可空，用于关联题目（需求标为"进阶"能力）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    CreatedAtMixin,
    PKMixin,
    SoftDeleteMixin,
    UpdatedAtMixin,
)

if TYPE_CHECKING:
    from .questions import Question


class Note(PKMixin, CreatedAtMixin, UpdatedAtMixin, SoftDeleteMixin, Base):
    __tablename__ = "notes"

    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联题目，可空。题目被物理删除时把关联置空，笔记本身保留。
    question_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("questions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    question: Mapped["Question | None"] = relationship(back_populates="notes")

    __table_args__ = (Index("ix_notes_updated", "deleted_at", "updated_at"),)
