"""question_tags —— 题目与标签的多对多关联表。requirements.md 3.5。

按需求文档该表只有 question_id / tag_id 两列，没有附加属性，
因此用 SQLAlchemy 的纯关联 Table（多对多关系的标准写法），
而不是带独立主键的模型类。

两列组成联合主键，两个外键都 ON DELETE CASCADE：
删题目或删标签都会自动清掉关联行。
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, Table

from .base import Base

question_tags = Table(
    "question_tags",
    Base.metadata,
    Column(
        "question_id",
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "tag_id",
        Integer,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
)
