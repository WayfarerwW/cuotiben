"""ORM 基类与公共列定义。

约定（AGENTS.md 第 3.1 / 3.3 节、requirements.md 第 3.10 节）：
  - 表名复数小写（folders、questions、review_records）
  - 所有 datetime 用 DateTime(timezone=True)，统一存 UTC
  - 外键一律 ON DELETE CASCADE
  - 删除用软删除（deleted_at），撤销打勾 = 软删除 review_record
  - 无 user_id（纯本地单机单用户）

关于时区的重要说明
------------------
SQLite 没有真正的"带时区时间"类型：SQLAlchemy 的 SQLAlchemy 原生 DATETIME
存进去时会把 tzinfo 丢掉，读出来是 **naive** datetime。如果直接比较
naive（读库的 now）与 aware（datetime.now(UTC)）会抛
`TypeError: can't compare offset-naive and offset-aware datetimes`，
而"待复习判断 next_review_at <= now()"正是本项目最核心的比较（AGENTS.md 4.2）。

所以这里用 UTCDateTime 统一收口：写入前统一转成 aware UTC，
读出后补回 UTC 时区，保证业务层拿到的永远是 aware datetime。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column
from sqlalchemy.types import TypeDecorator

# Base 定义在 app.database（与 engine / SessionLocal 放一起），这里转出，
# 使 `from app.models.base import Base` 与 `from app.models import Base` 都可用。
# 注意：app.database 不 import app.models，所以不存在循环导入。
from ..database import Base

__all__ = [
    "Base",
    "CreatedAtMixin",
    "PKMixin",
    "SoftDeleteMixin",
    "UTCDateTime",
    "UpdatedAtMixin",
    "fk_cascade",
    "utc_datetime_column",
    "utcnow",
]


def utcnow() -> datetime:
    """带时区的当前 UTC 时间。

    统一用它给 datetime 列做 Python 侧默认值，保证写入的都是 aware UTC，
    与 DateTime(timezone=True) 的声明一致（AGENTS.md 4.7：数据库存 UTC）。
    """
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """SQLite 下也能保证 aware UTC 往返的 datetime 类型。

    实现要点（踩过的坑）：
      - 不能用 TypeDecorator(impl=DateTime) + 返回字符串：SQLAlchemy 会把
        TypeDecorator 的 process_bind_param 结果再交给 impl 的
        process_bind_param，SQLite 的 DateTime 只接受 datetime/date 对象，
        于是报 "SQLite DateTime type only accepts Python datetime and date
        objects as input"。所以 impl 用 String，自己负责序列化。
      - 存成带 "+00:00" 的 ISO 字符串：直接用 sqlite3 命令行看数据时也能看出是 UTC。
      - 读出后补回 UTC，保证业务层拿到的永远是 aware datetime，
        这样 "next_review_at <= now()" 这种比较不会炸。
    """

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # naive 一律按 UTC 解释，避免把本地时间误当 UTC 存进去
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00:00"

    def process_result_value(self, value: Any, dialect: Dialect):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class PKMixin:
    """自增整型主键。"""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class CreatedAtMixin:
    """created_at 列，默认取当前 UTC 时间。"""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow
    )


class UpdatedAtMixin:
    """updated_at 列：插入时取当前时间，更新时自动刷新。"""

    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )


class SoftDeleteMixin:
    """软删除列：NULL 表示未删除，非 NULL 表示删除时间。"""

    deleted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True, default=None
    )


def utc_datetime_column(nullable: bool = False) -> MappedColumn:
    """普通（非默认值）UTC datetime 列，如 last_review_at / next_review_at。"""
    return mapped_column(UTCDateTime(), nullable=nullable)


def fk_cascade(target: str, *, nullable: bool = False) -> MappedColumn:
    """带 ON DELETE CASCADE 的外键列。

    SQLite 需要在每个连接上执行 `PRAGMA foreign_keys=ON` 才会真正生效，
    见 app/database.py 的 connect 事件。
    """
    return mapped_column(
        Integer,
        ForeignKey(target, ondelete="CASCADE"),
        nullable=nullable,
        index=True,
    )
