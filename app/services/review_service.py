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
from ..models.questions import MASTERY_MASTERED
from . import settings_service

logger = logging.getLogger(__name__)

# "今日"的时区：以本地时区 0 点为边界（requirements.md 5.7 / AGENTS.md 4.2）
DEFAULT_TIMEZONE = "Asia/Shanghai"
TIMEZONE_ENV = "CUOTIBEN_TIMEZONE"

# 逾期 >= 该天数折叠到"积压区"（requirements.md 2.9）
BACKLOG_DAYS = 14

# 打勾评价（requirements.md 2.14）：界面只给 2 档。
# 数据库里仍可能有历史遗留的 1/2，统一按"未完全掌握"处理。
MASTERY_LEVEL_NOT_MASTERED = 0      # 未完全掌握
MASTERY_LEVEL_MASTERED = 3          # 已掌握
MASTERY_STREAK_THRESHOLD = 4        # 连续几次「已掌握」即毕业（阶段数不足时兜底）


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
    #: 已连续达到「已掌握」的次数（0..阶段数）。界面用它显示"还差几次毕业"。
    mastery_streak: int = 0
    #: 连续几次即毕业（= 间隔序列阶段数），前端显示分母用。
    mastery_threshold: int = MASTERY_STREAK_THRESHOLD


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


def _resolve_interval_index(
    mastery: int | None, previous_streak: int, intervals: list[int]
) -> tuple[int, int]:
    """打勾后的 (interval_index, mastery_streak)。

    这是需求文档里曾经的自相矛盾点，现在是明确的（requirements 2.14 / 5.1）：

      - 5.1「推进」：按掌握程度推进阶段
      - 2.14：连续 N 次「已掌握」即毕业（N = 间隔序列的阶段数）

    规则（2026-10 用户确认）：

      - `mastery == MASTERY_LEVEL_MASTERED`（已掌握）
            streak += 1；interval_index = min(streak, 阶段数-1)
            即按时长递增：第 1 档 -> 第 2 档 -> … -> 末档
      - `mastery == MASTERY_LEVEL_NOT_MASTERED`（未完全掌握）
            streak = 0；interval_index = 0
            没掌握的题**尽快重做**，不该被拖到后面的长间隔
      - `mastery is None`（未表态，例如题目列表的「打勾」按钮）
            streak **保持不变**；interval_index = 0
            刻意不清零：用户在列表里随手打个勾，不该把复习页辛苦攒的
            连续进度抹掉（那是个很容易踩的坑）

    返回 (interval_index, streak)，streak 封顶在阶段数（达到即毕业）。
    """
    threshold = len(intervals) or MASTERY_STREAK_THRESHOLD
    last_index = max(len(intervals) - 1, 0)

    if mastery == MASTERY_LEVEL_MASTERED:
        streak = min(previous_streak + 1, threshold)
        return min(streak, last_index), streak

    if mastery is None:
        return 0, previous_streak

    # 未完全掌握，以及历史遗留的 1/2 档 —— 都按"没掌握"处理
    return 0, 0


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


def reset_mastery_progress(
    db: Session, question_id: int, *, now: datetime | None = None
) -> ReviewRecord | None:
    """把该题**当前生效那条**记录的连续掌握进度清掉并重排到第 1 档。

    什么时候用：用户手动把题目改回「未完全掌握」时（`question_service.set_mastery`）。
    不重排的话会出问题 —— 毕业那次打勾已经把 next_review_at 推到**末档**
    （比如 30 天后），只把状态改回「未完全掌握」并不能让它尽快回到复习队列，
    与"这题我还没掌握，要再练"的预期不符。

    只动当前生效那条；**历史记录保持原样**（审计痕迹，与软删除一样不改写过去）。

    不 commit，由调用方控制事务边界（要和题目状态一起提交）。
    """
    record = current_record(db, question_id)
    if record is None:
        return None
    record.mastery_streak = 0
    record.interval_index = 0
    record.next_review_at = settings_service.first_review_at(db, now=now)
    return record


def check(
    db: Session,
    question_id: int,
    *,
    mastery: int | None = None,
    now: datetime | None = None,
    tz: ZoneInfo | timezone | None = None,
) -> ReviewRecord:
    """打勾：任何题、任何时间都能打，不校验待复习状态，允许重复打。

    每次打勾**新增一条** review_record（不改旧记录），review_count 在上一轮
    基础上 +1。间隔与"连续掌握次数"按 `_resolve_interval_index` 的规则推进：

      - 已掌握(3)      -> 连续次数 +1，间隔逐档拉长
      - 未完全掌握(0)  -> 连续次数归零，间隔回第 1 档（尽快重做）
      - 未表态(None)   -> 连续次数不变，间隔回第 1 档

    **连续次数达到间隔序列的阶段数时，题目标记为「已掌握」并退出复习队列**
    （requirements 2.14）。已掌握的题不再出现在 list_today / count_due /
    backlog 里 —— 过滤在 `_due_rows` 一处完成。

    若该题当前记录已逾期（next_review_at 早于本地今日 0 点），本次打勾记为补卡
    （is_backfill=True），供"今日补卡数量 / 连续补卡天数"统计使用。
    """
    _get_question(db, question_id)
    moment = now or utcnow()

    previous = current_record(db, question_id)
    intervals = settings_service.get_intervals(db)
    previous_streak = previous.mastery_streak if previous is not None else 0
    index, streak = _resolve_interval_index(mastery, previous_streak, intervals)

    day_start, _day_end = today_bounds(moment, tz)
    was_overdue = (
        previous is not None
        and previous.next_review_at is not None
        and previous.next_review_at < day_start
    )

    record = ReviewRecord(
        question_id=question_id,
        review_count=(previous.review_count if previous else 0) + 1,
        interval_index=index,
        last_review_at=moment,
        next_review_at=moment + timedelta(days=intervals[index]),
        mastery_level=mastery if mastery is not None else 0,
        mastery_streak=streak,
        is_backfill=was_overdue,
    )
    db.add(record)

    # 连续攒够阶段数 -> 毕业：题目变「已掌握」，此后不进入复习队列。
    # 与记录写在同一个事务里，避免"记录说毕业了、题目状态还没改"的中间态。
    if streak >= len(intervals):
        question = db.get(Question, question_id)
        if question is not None and question.mastery_status != MASTERY_MASTERED:
            question.mastery_status = MASTERY_MASTERED
            logger.info("题目 %s 连续 %d 次已掌握，标记为已掌握（退出复习队列）",
                        question_id, streak)

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

    **已掌握的题被排除**（requirements 2.14：连续 N 次已掌握即毕业）。
    这里是"毕业就不再出现"的**唯一开关** —— list_today / count_due /
    backlog_question_ids / reset_backlog / 导出 review_queue 全部经由本函数，
    所以只在这一处过滤，口径必然一致。
    """
    ranked = (
        select(
            ReviewRecord.question_id.label("qid"),
            ReviewRecord.interval_index.label("idx"),
            ReviewRecord.next_review_at.label("next_at"),
            ReviewRecord.mastery_level.label("level"),
            ReviewRecord.mastery_streak.label("streak"),
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
        select(Question, ranked.c.idx, ranked.c.next_at, ranked.c.level,
               ranked.c.streak)
        .join(ranked, ranked.c.qid == Question.id)
        .where(
            ranked.c.rn == 1,
            ranked.c.next_at.is_not(None),
            ranked.c.next_at <= until,
            Question.deleted_at.is_(None),
            # 毕业的题不再进入任何"待复习"口径（requirements 2.14）
            Question.mastery_status != MASTERY_MASTERED,
        )
        .options(selectinload(Question.tags), selectinload(Question.images))
        .order_by(ranked.c.next_at)
    )

    out: list[tuple[Question, ReviewRecord]] = []
    for question, idx, next_at, level, streak in db.execute(stmt).all():
        out.append((
            question,
            ReviewRecord(
                question_id=question.id,
                interval_index=idx,
                next_review_at=next_at,
                mastery_level=level,
                mastery_streak=streak,
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
    threshold = len(settings_service.get_intervals(db)) or MASTERY_STREAK_THRESHOLD
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
                mastery_streak=record.mastery_streak or 0,
                mastery_threshold=threshold,
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


def backlog_question_ids(
    db: Session, *, now: datetime | None = None,
    tz: ZoneInfo | timezone | None = None,
) -> list[int]:
    """逾期 >= BACKLOG_DAYS 天的题目 id（"积压区"里的题）。

    判定基于每题"当前生效记录"的 next_review_at，与今日队列同口径。
    """
    moment = now or utcnow()
    day_start, day_end = today_bounds(moment, tz)
    rows = _due_rows(db, until=day_end)
    threshold = day_start - timedelta(days=BACKLOG_DAYS)
    return [
        question.id
        for question, record in rows
        if record.next_review_at is not None and record.next_review_at <= threshold
    ]


@dataclass
class BackfillResetResult:
    """一键重置积压的结果。"""

    affected_count: int
    affected_question_ids: list[int]
    mode: str
    spread_days: int | None = None
    first_due_at: datetime | None = None
    last_due_at: datetime | None = None


def reset_backlog(
    db: Session,
    *,
    spread: bool = False,
    spread_days: int | None = None,
    now: datetime | None = None,
    tz: ZoneInfo | timezone | None = None,
) -> BackfillResetResult:
    """一键重置积压（requirements.md 2.12）。

    目标：逾期 >= BACKLOG_DAYS（默认 14 天）的题，interval_index 归零。

    两种方式：
      - spread=False（默认）：全部重置到第一阶段 ——
        所有目标题的 interval_index=0，next_review_at = now() + INTERVALS[0]，
        即立刻回到常规复习节奏。
      - spread=True：分散重置到未来 N 天 ——
        同样 interval_index=0，但把 next_review_at 按天错开到
        [now, now + N 天] 区间内，避免积压题在同一天全部涌进今日队列。
        N 取 spread_days，未传则用 settings.backfill_reset_days（默认 14）。

    实现方式：**修改当前生效记录**而不是新增打勾记录 —— 这是"重新排期"，
    用户并没有复习这道题。同时把该记录标记 is_backfill=True 并刷新
    last_review_at，使"今日补卡数量 / 连续补卡天数"统计能算上这次重置。
    """
    moment = now or utcnow()
    intervals = settings_service.get_intervals(db)
    base_days = intervals[0]

    # 先算出实际生效的分散窗口，再判断有没有积压题。
    # 顺序很重要：否则"无积压题"的提前返回会跳过 days 的计算，
    # 让调用方拿到 spread_days=None，无法判断本次请求到底用的是多大的窗口。
    if spread:
        days = spread_days if spread_days is not None else (
            settings_service.get_backfill_reset_days(db)
        )
        days = max(1, int(days))
    else:
        days = 0

    wanted = backlog_question_ids(db, now=moment, tz=tz)
    if not wanted:
        return BackfillResetResult(affected_count=0, affected_question_ids=[],
                                   mode="spread" if spread else "all",
                                   spread_days=days if spread else None)

    # 保持队列顺序（重点优先 + 逾期最久）：错开时重点题排在更近的日期
    day_start, day_end = today_bounds(moment, tz)
    ordered: list[tuple[datetime, int]] = []
    for question, record in _due_rows(db, until=day_end):
        if question.id not in wanted or record.next_review_at is None:
            continue
        overdue_days = int((day_start - record.next_review_at).days)
        ordered.append((record.next_review_at, question.id, question.is_starred,
                        overdue_days))  # type: ignore[arg-type]
    ordered.sort(key=lambda t: (not t[2], -t[3], t[1]))
    ordered_ids = [t[1] for t in ordered]

    offsets: dict[int, int] = {}
    total = len(ordered_ids)
    for index, question_id in enumerate(ordered_ids):
        if days <= 0:
            offsets[question_id] = 0
        else:
            # 均分到 [0, days] 天：第一题今天，最后一题落在 days 天后
            offsets[question_id] = 0 if total <= 1 else round(index * days / (total - 1))

    target_ids = ordered_ids or wanted
    records = {
        r.question_id: r
        for r in db.scalars(
            select(ReviewRecord).where(
                ReviewRecord.question_id.in_(target_ids),
                ReviewRecord.deleted_at.is_(None),
            )
            .order_by(ReviewRecord.created_at, ReviewRecord.id)
        ).all()
    }
    # 同一题可能有多条未删除记录，只取最新那条（与 current_record 同口径）
    latest: dict[int, ReviewRecord] = {}
    for r in records.values():
        current = latest.get(r.question_id)
        if current is None or (r.created_at, r.id) > (current.created_at, current.id):
            latest[r.question_id] = r

    affected: list[int] = []
    due_times: list[datetime] = []
    for question_id in target_ids:
        record = latest.get(question_id)
        if record is None:
            continue
        offset = offsets.get(question_id, 0)
        record.interval_index = 0
        record.next_review_at = moment + timedelta(days=base_days + offset)
        record.last_review_at = moment
        record.is_backfill = True
        affected.append(question_id)
        due_times.append(record.next_review_at)

    db.commit()
    return BackfillResetResult(
        affected_count=len(affected),
        affected_question_ids=affected,
        mode="spread" if spread else "all",
        spread_days=days if spread else None,
        first_due_at=min(due_times) if due_times else None,
        last_due_at=max(due_times) if due_times else None,
    )


# --------------------------------------------------------------------------
# 补卡统计（requirements.md 2.12）
# --------------------------------------------------------------------------


def _count_backfill_records(db: Session, day_start: datetime, day_end: datetime) -> int:
    """某一天内的补卡次数（以 last_review_at 落在该天为准）。"""
    return int(
        db.scalar(
            select(func.count(ReviewRecord.id)).where(
                ReviewRecord.deleted_at.is_(None),
                ReviewRecord.is_backfill.is_(True),
                ReviewRecord.last_review_at.is_not(None),
                ReviewRecord.last_review_at >= day_start,
                ReviewRecord.last_review_at < day_end,
            )
        ) or 0
    )


def _consecutive_backfill_days(
    db: Session, *, now: datetime, tz: ZoneInfo | timezone | None = None,
    lookback: int = 365,
) -> int:
    """连续补卡天数：从今天往前数，连续多少天每天至少有一次补卡。

    今天还没补卡时从昨天起算（否则每天早上打开应用都会看到连续天数被清零，
    不符合"连续"的直觉）。
    """
    zone = tz or local_timezone()
    if _count_backfill_records(db, *today_bounds(now, zone)):
        cursor_date = now.astimezone(zone).date()
    else:
        # 今天还没补卡，从昨天起算
        cursor_date = now.astimezone(zone).date() - timedelta(days=1)

    streak = 0
    for _ in range(lookback):
        start = datetime.combine(cursor_date, time.min, tzinfo=zone).astimezone(UTC)
        end = datetime.combine(
            cursor_date + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(UTC)
        if _count_backfill_records(db, start, end) <= 0:
            break
        streak += 1
        cursor_date -= timedelta(days=1)
    return streak


def stats(
    db: Session, *, now: datetime | None = None, tz: ZoneInfo | timezone | None = None
) -> dict:
    """补卡统计（requirements.md 2.12 / 4.5 GET /review/backfill/stats）。

    - today_backfill_count：今日补卡数量（今天打勾且当时已逾期，或今日被重置积压）
    - consecutive_days：连续补卡天数
    - backlog_count：当前仍处于积压区（逾期 >=14 天）的题目数
    """
    moment = now or utcnow()
    start, end = today_bounds(moment, tz)
    return {
        "today_backfill_count": _count_backfill_records(db, start, end),
        "consecutive_days": _consecutive_backfill_days(db, now=moment, tz=tz),
        "backlog_count": len(backlog_question_ids(db, now=moment, tz=tz)),
    }
