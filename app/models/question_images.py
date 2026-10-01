"""question_images —— 题目图片。requirements.md 3.3 / 2.6 / 2.7。

数据库只存相对路径，物理文件在 uploads/YYYY/MM/DD/{uuid}.jpg。
原图保留与否取决于配置，original_path 可空。
图片物理文件的删除放后台异步任务，不在这里处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PKMixin, fk_cascade

if TYPE_CHECKING:
    from .questions import Question


class QuestionImage(PKMixin, CreatedAtMixin, Base):
    __tablename__ = "question_images"

    # 可空：上传接口先落记录（此时还没录题），拿到 id 后前端才用该图片建题。
    # 需求 4.4 的 DELETE /upload/image/{id} 需要这个主键，所以不能等建题时才建行。
    question_id: Mapped[int | None] = fk_cascade("questions.id", nullable=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 可空：未挂题的孤儿图片（上传后还没录题）
    question: Mapped["Question | None"] = relationship(back_populates="images")
