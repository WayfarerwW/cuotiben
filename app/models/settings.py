"""settings —— 键值配置。requirements.md 3.8。

默认配置（第 3.8 节）：
  intervals          = [3, 7, 15, 30]  记忆曲线间隔（天）
  backfill_limit     = 20              每日补卡上限
  backfill_reset_days= 14              逾期 >=14 天折叠到积压区

value 用 TEXT 存字符串；复杂值（如 intervals 列表）存 JSON 字符串，
读写封装放在 app/services/settings_service.py（未实现前不要在各处自行 json.loads）。
"""

from __future__ import annotations

import json

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PKMixin

KEY_INTERVALS = "intervals"
KEY_BACKFILL_LIMIT = "backfill_limit"
KEY_BACKFILL_RESET_DAYS = "backfill_reset_days"

DEFAULT_INTERVALS: list[int] = [3, 7, 15, 30]
DEFAULT_BACKFILL_LIMIT = 20
DEFAULT_BACKFILL_RESET_DAYS = 14

# 首次初始化时写入的默认值（值为字符串形式，intervals 存 JSON）
DEFAULT_SETTINGS: dict[str, str] = {
    KEY_INTERVALS: json.dumps(DEFAULT_INTERVALS),
    KEY_BACKFILL_LIMIT: str(DEFAULT_BACKFILL_LIMIT),
    KEY_BACKFILL_RESET_DAYS: str(DEFAULT_BACKFILL_RESET_DAYS),
}


class Setting(PKMixin, Base):
    __tablename__ = "settings"

    # key 唯一；unique=True 已隐含索引，不再单独声明 Index
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
