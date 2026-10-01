"""ORM 模型包。

集中导入所有模型，保证 Base.metadata 完整 —— create_all / Alembic 迁移都依赖这一点。
新增模型时必须在下面加 import，否则表不会建出来。

表清单（requirements.md 第 3 节）：
    folders          3.1
    questions        3.2
    question_images  3.3
    tags             3.4
    question_tags    3.5  （纯关联表）
    review_records   3.6
    notes            3.7
    settings         3.8
    export_records   3.9
"""

from __future__ import annotations

from .base import Base, utcnow
from .export_records import ExportRecord
from .folders import LEVEL_CATEGORY, LEVEL_SUBJECT, Folder
from .notes import Note
from .question_images import QuestionImage
from .question_tags import question_tags
from .questions import MASTERY_MASTERED, MASTERY_STILL_WRONG, Question
from .review_records import ReviewRecord
from .settings import (
    DEFAULT_BACKFILL_LIMIT,
    DEFAULT_BACKFILL_RESET_DAYS,
    DEFAULT_INTERVALS,
    DEFAULT_SETTINGS,
    KEY_BACKFILL_LIMIT,
    KEY_BACKFILL_RESET_DAYS,
    KEY_INTERVALS,
    Setting,
)
from .tags import Tag

__all__ = [
    # 基类
    "Base",
    "utcnow",
    # 表
    "Folder",
    "Question",
    "QuestionImage",
    "Tag",
    "question_tags",
    "ReviewRecord",
    "Note",
    "Setting",
    "ExportRecord",
    # 常量
    "LEVEL_SUBJECT",
    "LEVEL_CATEGORY",
    "MASTERY_STILL_WRONG",
    "MASTERY_MASTERED",
    "KEY_INTERVALS",
    "KEY_BACKFILL_LIMIT",
    "KEY_BACKFILL_RESET_DAYS",
    "DEFAULT_INTERVALS",
    "DEFAULT_BACKFILL_LIMIT",
    "DEFAULT_BACKFILL_RESET_DAYS",
    "DEFAULT_SETTINGS",
]
