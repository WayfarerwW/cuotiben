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
    fields_to_update: set[str] | None = None,
) -> None:
    """更新配置（requirements.md 4.8）。

    传入 `fields_to_update`（路由用 `set(payload.model_fields_set)`）时，
    区分"未传"与"传 null"：

      - 未传    -> 保持原值
      - 传 null -> **删除该配置项，回退到默认值**
      - 传具体值 -> 写入该值

    校验的是**合并后的最终值**，而不是只校验本次传进来的值。
    否则把 intervals 置 null 会绕过"严格递增"检查，写入一个非递增序列。
    intervals 必须为正整数且严格递增（requirements.md 2.10，API 层也有同样校验）。
    """
    explicit = fields_to_update is not None

    def was_sent(field: str, value: object) -> bool:
        return field in fields_to_update if explicit else value is not None

    incoming = {
        KEY_INTERVALS: intervals,
        KEY_BACKFILL_LIMIT: backfill_limit,
        KEY_BACKFILL_RESET_DAYS: backfill_reset_days,
    }

    # 先算出每个 key 的"目标状态"：SET(值) / DELETE(回退默认) / 不动
    actions: dict[str, object] = {}
    for key, value in incoming.items():
        if not was_sent(key, value):
            continue
        actions[key] = value  # None 表示删除该 key

    # 校验合并后的最终值
    if KEY_INTERVALS in actions:
        candidate = actions[KEY_INTERVALS]
        if candidate is None:
            candidate = DEFAULT_INTERVALS  # 回退默认
        if not isinstance(candidate, list) or not candidate:
            raise SettingsError("intervals 必须是非空数组")
        if any(v <= 0 for v in candidate):
            raise SettingsError("intervals 必须是正整数天数")
        if any(b <= a for a, b in pairwise(candidate)):
            raise SettingsError("intervals 必须严格递增")

    if KEY_BACKFILL_LIMIT in actions:
        candidate = actions[KEY_BACKFILL_LIMIT]
        if candidate is None:
            candidate = DEFAULT_BACKFILL_LIMIT
        if candidate < 0:
            raise SettingsError("backfill_limit 不能为负")
        if candidate > 500:
            raise SettingsError("backfill_limit 过大")

    if KEY_BACKFILL_RESET_DAYS in actions:
        candidate = actions[KEY_BACKFILL_RESET_DAYS]
        if candidate is None:
            candidate = DEFAULT_BACKFILL_RESET_DAYS
        if candidate < 1:
            raise SettingsError("backfill_reset_days 至少为 1")
        if candidate > 365:
            raise SettingsError("backfill_reset_days 过大")

    if not actions:
        return

    for key, value in actions.items():
        row = db.scalars(select(Setting).where(Setting.key == key)).one_or_none()
        if value is None:
            # 回退默认 = 删掉该行；getter 会自动返回默认值
            if row is not None:
                db.delete(row)
            continue
        stored = json.dumps(value) if key == KEY_INTERVALS else str(value)
        if row is None:
            db.add(Setting(key=key, value=stored))
        else:
            row.value = stored
    db.commit()
