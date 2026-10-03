"""review_records —— 复习记录。requirements.md 3.6 / 5.1 / 5.2 / 5.3。

一条 record 代表"打过一次勾"。关键规则（AGENTS.md 4.1）：
  - 任何题、任何时间都能打勾，不校验是否处于待复习状态
  - 打勾新增一条记录，允许重复打勾
  - 打勾按 mastery_level 逐档推进 interval_index（requirements 5.1）
  - 撤销 = 软删除最近一条 review_record（deleted_at），上一轮自动生效

因此同一 question 会有多条记录，取"当前生效的那条"时应过滤 deleted_at IS NULL
并按 created_at/id 取最新，不覆盖历史。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, Integer
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

# 间隔索引范围 0~3，对应 settings.intervals 的四个阶段（天）
INTERVAL_INDEX_MIN = 0
INTERVAL_INDEX_MAX = 3

#: 连续达到「已掌握」多少次即毕业（题目变 mastered、退出复习队列）。
#: 与 settings.intervals 的**阶段数**一致：四个阶段就要连续四次走完。
#: requirements.md 2.14 规定。
MASTERY_STREAK_THRESHOLD = 4


class ReviewRecord(PKMixin, CreatedAtMixin, SoftDeleteMixin, Base):
    __tablename__ = "review_records"

    question_id: Mapped[int] = fk_cascade("questions.id")
    # 该题累计已复习次数（打勾时递增）
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    interval_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 两者都可空：首条记录是"排定首次复习"，此时还没复习过，last_review_at 为 NULL。
    last_review_at: Mapped[datetime | None] = utc_datetime_column(nullable=True)
    # next_review_at 允许为空是为了容忍数据被外部改坏的情况；
    # 正常流程（创建题目、打勾）都会写入具体时间。
    next_review_at: Mapped[datetime | None] = utc_datetime_column(nullable=True)
    # mastery_level: 打勾评价。界面只用 2 档（0=未完全掌握 / 3=已掌握），
    # 但字段仍接受 0/1/2/3 —— 历史数据里有 1/2，见 requirements.md 2.14。
    mastery_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 已**连续**达到「已掌握」的次数（0..MASTERY_STREAK_THRESHOLD）。
    # 中间点一次「未完全掌握」就归零，要重新连续攒够才算毕业。
    # 为什么固化成字段而不是回扫历史：撤销会软删除记录，回扫的语义容易算错；
    # 而且队列页会频繁读取，回扫有 N+1 风险。
    mastery_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # 本次是否属于"补卡"（题目当时已逾期，或由一键重置积压产生）。
    # 需求 2.12 要统计"今日补卡数量、连续补卡天数"。若靠"回看上一条记录的
    # next_review_at"反推，在记录被撤销/软删除后会算错，所以打勾时就固化下来。
    is_backfill: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    question: Mapped["Question"] = relationship(back_populates="review_records")

    __table_args__ = (
        # 待复习队列的取数路径：next_review_at <= now() 且未软删除，按时间排序
        Index("ix_review_records_due", "deleted_at", "next_review_at"),
        Index("ix_review_records_question", "question_id", "deleted_at"),
    )
