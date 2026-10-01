"""复习业务逻辑（requirements.md 2.9 / 2.10 / 2.11 / 2.12 / 5.1 / 5.2 / 5.3）。

核心规则（AGENTS.md 4.1，最容易被"防重复"直觉带错的地方）：
  - **不校验是否处于待复习状态**：任何题、任何时间都能打勾
  - 打勾后 interval_index **重置为 0**，next_review_at = now() + INTERVALS[0]（默认 3 天）
  - **允许重复打勾，不返回 409**；每次打勾新增一条 review_record
  - 撤销 = 软删除最近一条 review_record，上一轮自动生效

关于 5.1「推进」与 5.2「打勾」的取舍见 `_resolve_interval_index` 的注释。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Folder, Question, QuestionImage, ReviewRecord, Tag
from ..models.base import utcnow
from . import settings_service

logger = logging.getLogger(__name__)

# "今日"的时区：以本地时区 0 点为边界（requirements.md 5.7 / AGENTS.md 4.2）
DEFAULT_TIMEZONE = "Asia/Shanghai"
TIMEZONE_ENV = "CUOTIBEN_TIMEZONE"

# 逾期 >= 该天数折叠到"积压区"（requirements.md 2.9）
BACKLOG_DAYS = 14


class ReviewError(Exception):
    """复习相关业务异常基类。"""


class ReviewQuestionNotFoundError(ReviewError):
    """题目不存在（或已软删除）。"""


class NothingToUndoError(ReviewError):
    """没有可撤销的打勾记录。"""


@dataclass
class ReviewItem:
    """今日队列中的一道题（requirements.md 2.9 / 4.5）。"""

    question_id: int
    stem: str | None
    answer: str | None
    is_starred: bool
    mastery_status: str
    folder_id: int
    folder_name: str | None
    interval_index: int
    next_review_at: datetime | None
    images: list[QuestionImage] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    overdue_days: int = 0
    is_backlog: bool = False
    is_overdue: bool = False


# --------------------------------------------------------------------------
# 时区与"今日"边界
# --------------------------------------------------------------------------


def local_timezone() -> ZoneInfo | timezone:
    """本地时区。可用 CUOTIBEN_TIMEZONE 覆盖（测试用）。

    Windows 没有系统 IANA 时区库，zoneinfo 依赖 tzdata 包（已列入
    requirements.txt）。万一 tzdata 缺失，这里退化为固定 UTC+8 偏移而不是
    抛异常 —— "今日"边界会因此在夏令时地区略有偏差，但总好过整个复习模块不可用。
    """
    name = os.environ.get(TIMEZONE_ENV, DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "时区 %r 不可用（可能缺 tzdata 包），退化为固定 UTC+8 偏移", name
        )
        return timezone(timedelta(hours=8))


def today_bounds(
    now: datetime | None = None, tz: ZoneInfo | timezone | None = None
) -> tuple[datetime, datetime]:
    """本地时区"今日"的 [起, 止) 区间，返回 UTC 时间。

    做法：取本地日期 -> 组合当天 00:00 -> 挂本地时区 -> 转 UTC。
    比"直接减 24 小时"稳：夏令时切换那天不会是 24 小时。
    """
    zone = tz or local_timezone()
    moment = now or utcnow()
    local_date = moment.astimezone(zone).date()
    start_local = datetime.combine(local_date, time.min, tzinfo=zone)
    end_local = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


# --------------------------------------------------------------------------
# 创建首条记录（requirements.md 2.10）
# --------------------------------------------------------------------------


def create_initial_record(
    db: Session, question_id: int, *, now: datetime | None = None
) -> ReviewRecord:
    """为新题生成首条 review_record：interval_index=0，next_review_at=now()+3天。

    last_review_at 保持 None —— 这条记录只是"排定首次复习"，题目还没被复习过。
    不 commit，由调用方控制事务边界。
    """
    record = ReviewRecord(
        question_id=question_id,
        review_count=0,
        interval_index=0,
        last_review_at=None,
        next_review_at=settings_service.first_review_at(db, now=now),
        mastery_level=0,
    )
    db.add(record)
    return record


# --------------------------------------------------------------------------
# 当前生效的记录
# --------------------------------------------------------------------------


def current_record(db: Session, question_id: int) -> ReviewRecord | None:
    """当前生效的复习记录 = 未软删除里最新的一条。

    打勾会累积多条记录，撤销是软删除最近一条，所以"当前状态"必须按
    created_at/id 取最新，而不是取第一条。
    """
    return db.scalars(
        select(ReviewRecord)
        .where(
            ReviewRecord.question_id == question_id,
            ReviewRecord.deleted_at.is_(None),
        )
        .order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc())
        .limit(1)
    ).one_or_none()


def _resolve_interval_index(mastery: int | None) -> int:
    """打勾后的 interval_index。

    这是需求文档内部的一个取舍点：
      - 5.2「打勾（随时可打，重置阶段）」写死 `new_interval_index = 0`，
        AGENTS.md 4.1 也规定"打勾后 interval_index 重置为 0"。
      - 5.1「推进」规则（mastery 1 维持 / 2 +1 / 3 +2）是"按掌握程度推进阶段"。
      - 2.11 把两者描述为"两种模式（择一）"：简单模式=重置到 0；
        精细模式=按掌握程度决定阶段。

    当前取 5.2 / 4.1 的口径：**总是重置为 0**；mastery 仍照常写入
    mastery_level 供统计与将来启用精细模式使用。要切精细模式，只改本函数。
    """
    return 0


# --------------------------------------------------------------------------
# 打勾 / 撤销
# --------------------------------------------------------------------------


def _get_question(db: Session, question_id: int) -> Question:
    question = db.scalars(
        select(Question).where(
            Question.id == question_id, Question.deleted_at.is_(None)
        )
    ).one_or_none()
    if question is None:
        raise ReviewQuestionNotFoundError(f"题目 {question_id} 不存在")
    return question


def check(
    db: Session,
    question_id: int,
    *,
    mastery: int | None = None,
    now: datetime | None = None,
) -> ReviewRecord:
    """打勾：任何题、任何时间都能打，不校验待复习状态，允许重复打。

    每次打勾**新增一条** review_record（不改旧记录），interval_index 重置为 0，
    next_review_at = now() + INTERVALS[0]，review_count 在上一轮基础上 +1。
    """
    _get_question(db, question_id)
    moment = now or utcnow()

    previous = current_record(db, question_id)
    intervals = settings_service.get_intervals(db)
    index = _resolve_interval_index(mastery)

    record = ReviewRecord(
        question_id=question_id,
        review_count=(previous.review_count if previous else 0) + 1,
        interval_index=index,
        last_review_at=moment,
        next_review_at=moment + timedelta(days=intervals[index]),
        mastery_level=mastery if mastery is not None else 0,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def uncheck(db: Session, question_id: int) -> ReviewRecord:
    """撤销打勾：软删除最近一条 review_record（requirements.md 5.3）。

    软删除后上一轮记录自然重新生效，不需要额外的回滚逻辑。
    没有可撤销记录时抛 NothingToUndoError（router 转 409）。
    """
    _get_question(db, question_id)
    latest = current_record(db, question_id)
    if latest is None:
        raise NothingToUndoError(f"题目 {question_id} 没有可撤销的打勾记录")

    latest.deleted_at = utcnow()
    db.commit()
    db.refresh(latest)
    return latest


# --------------------------------------------------------------------------
# 今日队列（requirements.md 2.9 / 2.12）
# --------------------------------------------------------------------------


def _due_rows(db: Session, *, until: datetime) -> list[tuple[Question, ReviewRecord]]:
    """取"当前生效记录"的 next_review_at <= until 的题目。

    一题可能有多条历史记录，只有最新那条未删除的算数：用 row_number()
    一次挑出每题最新记录，避免逐题查询（列表页会 N+1）。
    返回的 ReviewRecord 是承载最新记录字段的轻量对象（不作为持久化实体使用）。
    """
    ranked = (
        select(
            ReviewRecord.question_id.label("qid"),
            ReviewRecord.interval_index.label("idx"),
            ReviewRecord.next_review_at.label("next_at"),
            ReviewRecord.mastery_level.label("level"),
            func.row_number()
            .over(
                partition_by=ReviewRecord.question_id,
                order_by=(ReviewRecord.created_at.desc(), ReviewRecord.id.desc()),
            )
            .label("rn"),
        )
        .where(ReviewRecord.deleted_at.is_(None))
        .subquery()
    )

    stmt: Select = (
        select(Question, ranked.c.idx, ranked.c.next_at, ranked.c.level)
        .join(ranked, ranked.c.qid == Question.id)
        .where(
            ranked.c.rn == 1,
            ranked.c.next_at.is_not(None),
            ranked.c.next_at <= until,
            Question.deleted_at.is_(None),
        )
        .options(selectinload(Question.tags), selectinload(Question.images))
        .order_by(ranked.c.next_at)
    )

    out: list[tuple[Question, ReviewRecord]] = []
    for question, idx, next_at, level in db.execute(stmt).all():
        out.append((
            question,
            ReviewRecord(
                question_id=question.id,
                interval_index=idx,
                next_review_at=next_at,
                mastery_level=level,
            ),
        ))
    return out


def _build_items(
    db: Session, rows: list[tuple[Question, ReviewRecord]], *, now: datetime
) -> list[ReviewItem]:
    """组装复习项，批量补 folder_name，并算出逾期天数与积压标记。"""
    folder_ids = {q.folder_id for q, _ in rows}
    folder_names: dict[int, str] = {}
    if folder_ids:
        folder_names = dict(
            db.execute(
                select(Folder.id, Folder.name).where(Folder.id.in_(folder_ids))
            ).all()
        )

    items: list[ReviewItem] = []
    for question, record in rows:
        next_at = record.next_review_at
        overdue_days = 0
        if next_at is not None and next_at < now:
            overdue_days = max(0, (now - next_at).days)
        items.append(
            ReviewItem(
                question_id=question.id,
                stem=question.stem,
                answer=question.answer,
                is_starred=question.is_starred,
                mastery_status=question.mastery_status,
                folder_id=question.folder_id,
                folder_name=folder_names.get(question.folder_id),
                interval_index=record.interval_index or 0,
                next_review_at=next_at,
                images=list(question.images),
                tags=list(question.tags),
                overdue_days=overdue_days,
                is_backlog=overdue_days >= BACKLOG_DAYS,
                is_overdue=overdue_days > 0,
            )
        )
    return items


def list_today(
    db: Session, *, now: datetime | None = None, tz: ZoneInfo | None = None
) -> tuple[list[ReviewItem], dict]:
    """今日队列 = 今日到期 + 最多 N 道逾期补卡（requirements.md 2.12）。

    "今日"以本地时区 0 点为边界：今天 00:00 之后到期的算"今日到期"；
    更早到期的属逾期，按"重点优先 + 逾期最久"排序，最多取 backfill_limit 道。

    返回 (items, meta)。
    """
    moment = now or utcnow()
    start, end = today_bounds(moment, tz)
    limit = settings_service.get_backfill_limit(db)

    rows = _due_rows(db, until=end)
    items = _build_items(db, rows, now=moment)

    due = [i for i in items if i.next_review_at is not None and i.next_review_at >= start]
    overdue = [i for i in items if i.next_review_at is not None and i.next_review_at < start]

    # 逾期题：重点优先，其次逾期最久
    overdue.sort(key=lambda i: (not i.is_starred, -i.overdue_days, i.question_id))
    backfill = overdue[:limit] if limit > 0 else []

    # 展示顺序：重点优先，其次按到期时间
    result = sorted(
        due + backfill,
        key=lambda i: (not i.is_starred, i.next_review_at or moment, i.question_id),
    )
    meta = {
        "due_count": len(due),
        "backfill_count": len(backfill),
        "backfill_limit": limit,
        "overdue_total": len(overdue),
        "total": len(result),
    }
    return result, meta


def count_due(
    db: Session, *, now: datetime | None = None, tz: ZoneInfo | None = None
) -> int:
    """今日队列数量（铃铛角标用，requirements.md 4.5 GET /review/count）。

    与 list_today 同口径：今日到期 + 补卡上限内的逾期题。
    """
    items, _meta = list_today(db, now=now, tz=tz)
    return len(items)


def stats(db: Session, *, now: datetime | None = None) -> dict:
    """补卡统计（requirements.md 2.12）：今日补卡数、积压题数。"""
    moment = now or utcnow()
    start, end = today_bounds(moment)

    today_checked = db.scalar(
        select(func.count(ReviewRecord.id)).where(
            ReviewRecord.deleted_at.is_(None),
            ReviewRecord.last_review_at.is_not(None),
            ReviewRecord.last_review_at >= start,
            ReviewRecord.last_review_at < end,
        )
    ) or 0

    _items, meta = list_today(db, now=moment)
    return {
        "today_backfill_count": int(today_checked),
        "backlog_count": int(meta["overdue_total"]),
    }
