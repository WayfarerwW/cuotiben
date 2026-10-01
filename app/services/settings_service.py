"""settings 业务逻辑（requirements.md 3.8 / 4.8 / 5.1）。

配置以键值对存在 settings 表，value 是 TEXT。复杂值（intervals 列表）存 JSON
字符串 —— 读写都收敛到本模块，不要在别处自行 json.loads。

注意：``ensure_default_settings`` 原在 app/main.py，按 AGENTS.md 3.4
「业务逻辑一律放 services/」迁到这里；main.py 只保留调用。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    DEFAULT_BACKFILL_LIMIT,
    DEFAULT_BACKFILL_RESET_DAYS,
    DEFAULT_INTERVALS,
    DEFAULT_SETTINGS,
    KEY_BACKFILL_LIMIT,
    KEY_BACKFILL_RESET_DAYS,
    KEY_INTERVALS,
    Setting,
)
from ..models.base import utcnow


class SettingsError(Exception):
    """settings 相关业务异常。"""


# --------------------------------------------------------------------------
# 初始化
# --------------------------------------------------------------------------


def ensure_default_settings(db: Session) -> list[str]:
    """写入缺失的默认配置，返回本次新增的 key 列表。

    幂等：已存在的 key 一律不覆盖，避免把用户改过的配置重置回默认值。
    """
    existing = set(db.scalars(select(Setting.key)).all())
    created: list[str] = []
    for key, value in DEFAULT_SETTINGS.items():
        if key in existing:
            continue
        db.add(Setting(key=key, value=value))
        created.append(key)
    if created:
        db.commit()
    return created


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------


def _get_raw(db: Session, key: str) -> str | None:
    row = db.scalars(select(Setting).where(Setting.key == key)).one_or_none()
    return row.value if row else None


def get_intervals(db: Session) -> list[int]:
    """记忆曲线间隔（天）。缺失或损坏时回退到默认 [3, 7, 15, 30]。

    这里的容错是有意为之：配置坏掉不应该让"新建题目"整个失败，
    复习逻辑退化成默认间隔远好过接口报 500。
    """
    raw = _get_raw(db, KEY_INTERVALS)
    if not raw:
        return list(DEFAULT_INTERVALS)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return list(DEFAULT_INTERVALS)
    if not isinstance(value, list) or not value:
        return list(DEFAULT_INTERVALS)
    try:
        intervals = [int(v) for v in value]
    except (TypeError, ValueError):
        return list(DEFAULT_INTERVALS)
    if any(v <= 0 for v in intervals):
        return list(DEFAULT_INTERVALS)
    return intervals


def get_backfill_limit(db: Session) -> int:
    raw = _get_raw(db, KEY_BACKFILL_LIMIT)
    try:
        return int(raw) if raw is not None else DEFAULT_BACKFILL_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_BACKFILL_LIMIT


def get_backfill_reset_days(db: Session) -> int:
    raw = _get_raw(db, KEY_BACKFILL_RESET_DAYS)
    try:
        return int(raw) if raw is not None else DEFAULT_BACKFILL_RESET_DAYS
    except (TypeError, ValueError):
        return DEFAULT_BACKFILL_RESET_DAYS


def first_interval_days(db: Session) -> int:
    """首个复习间隔（默认 3 天，不是 1 天 —— AGENTS.md 4.2）。"""
    return get_intervals(db)[0]


def get_all_settings(db: Session) -> dict:
    """一次性读出全部业务配置（requirements.md 4.8 GET /settings）。

    缺失的 key 由各 getter 回退到默认值，因此整体不会被"库不完整"影响。
    不在这里写库 —— 写入默认值是启动时的职责（ensure_default_settings）。
    """
    return {
        KEY_INTERVALS: get_intervals(db),
        KEY_BACKFILL_LIMIT: get_backfill_limit(db),
        KEY_BACKFILL_RESET_DAYS: get_backfill_reset_days(db),
    }


def first_review_at(db: Session, *, now: datetime | None = None) -> datetime:
    """新建题目时首条 review_record 的 next_review_at。

    需求 2.10：题目创建时自动生成首条记录，next_review_at = now() + 3天。
    """
    base = now or utcnow()
    return base + timedelta(days=first_interval_days(db))


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------


def update_settings(
    db: Session,
    *,
    intervals: list[int] | None = None,
    backfill_limit: int | None = None,
    backfill_reset_days: int | None = None,
) -> None:
    """更新配置（只改传入项）。

    校验放在这里而不是只靠 Pydantic：写库的路径可能不止 API 一条。
    intervals 必须为正整数且严格递增 —— 相同或递减的间隔没有记忆曲线语义
    （requirements.md 2.10，API 层也有同样校验）。
    """
    if intervals is not None:
        if len(intervals) < 1 or any(v <= 0 for v in intervals):
            raise SettingsError("intervals 必须是正整数天数")
        if any(b <= a for a, b in pairwise(intervals)):
            raise SettingsError("intervals 必须严格递增")

    updates: dict[str, str] = {}
    if intervals is not None:
        updates[KEY_INTERVALS] = json.dumps(intervals)
    if backfill_limit is not None:
        if backfill_limit < 0:
            raise SettingsError("backfill_limit 不能为负")
        updates[KEY_BACKFILL_LIMIT] = str(backfill_limit)
    if backfill_reset_days is not None:
        if backfill_reset_days < 1:
            raise SettingsError("backfill_reset_days 至少为 1")
        updates[KEY_BACKFILL_RESET_DAYS] = str(backfill_reset_days)

    for key, value in updates.items():
        row = db.scalars(select(Setting).where(Setting.key == key)).one_or_none()
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    if updates:
        db.commit()
