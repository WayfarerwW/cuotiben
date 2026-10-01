"""review_records —— 复习记录。requirements.md 3.6 / 5.1 / 5.2 / 5.3。

一条 record 代表"打过一次勾"。关键规则（AGENTS.md 4.1）：
  - 任何题、任何时间都能打勾，不校验是否处于待复习状态
  - 打勾新增一条记录，interval_index 固定为 0，允许重复打勾
  - 撤销 = 软删除最近一条 review_record（deleted_at），上一轮自动生效

因此同一 question 会有多条记录，取"当前生效的那条"时应过滤 deleted_at IS NULL
并按 created_at/id 取最新，不覆盖历史。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    CreatedAtMixin,
    PKMixin,
    SoftDeleteMixin,
    fk_cascade,
    utc_datetime_column,
)

if TYPE_CHECKING:
    from .questions import Question

# 记忆曲线间隔索引范围 0~3，对应 settings.intervals = [3, 7, 15, 30]（天）
INTERVAL_INDEX_MIN = 0
INTERVAL_INDEX_MAX = 3


class ReviewRecord(PKMixin, CreatedAtMixin, SoftDeleteMixin, Base):
    __tablename__ = "review_records"

    question_id: Mapped[int] = fk_cascade("questions.id")
    # 该题累计已复习次数（打勾时递增）
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interval_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_review_at: Mapped[datetime | None] = utc_datetime_column()
    next_review_at: Mapped[datetime | None] = utc_datetime_column()
    # mastery_level: 0/1/2/3，见 requirements.md 5.1 推进规则
    mastery_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    question: Mapped["Question"] = relationship(back_populates="review_records")

    __table_args__ = (
        # 待复习队列的取数路径：next_review_at <= now() 且未软删除，按时间排序
        Index("ix_review_records_due", "deleted_at", "next_review_at"),
        Index("ix_review_records_question", "question_id", "deleted_at"),
    )
