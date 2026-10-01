"""export_records —— PDF 导出历史（需求文档标为可选）。requirements.md 3.9。

记录每次导出的范围描述、题目数量与文件路径，便于"数据说明页"回溯。
导出文件落在 backups/ 或用户指定目录，这里只存路径。
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, PKMixin


class ExportRecord(PKMixin, CreatedAtMixin, Base):
    __tablename__ = "export_records"

    range_desc: Mapped[str | None] = mapped_column(String(300), nullable=True)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (Index("ix_export_records_created", "created_at"),)
