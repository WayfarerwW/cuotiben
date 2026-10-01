"""tags —— 标签。requirements.md 3.4 / 2.4 / 5.4。

name 入库前必须归一化：trim + 小写 + 全半角转换（tags.name 唯一）。
归一化函数属于业务逻辑，放在 app/services/tag_service.py。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PKMixin
from .question_tags import question_tags

if TYPE_CHECKING:
    from .questions import Question


class Tag(PKMixin, CreatedAtMixin, Base):
    __tablename__ = "tags"

    # 唯一约束即索引，不再单独 index=True
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    questions: Mapped[list["Question"]] = relationship(
        secondary=question_tags,
        back_populates="tags",
    )
