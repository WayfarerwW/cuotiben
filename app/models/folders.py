"""folders —— 两级文件夹（一级=学科，二级=大类）。requirements.md 3.1 / 2.2。

题目只挂在二级文件夹（level=2）下；同一父下不允许同名，由应用层校验。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, PKMixin, SoftDeleteMixin, fk_cascade

if TYPE_CHECKING:
    from .questions import Question

LEVEL_SUBJECT = 1  # 学科
LEVEL_CATEGORY = 2  # 大类


class Folder(PKMixin, CreatedAtMixin, SoftDeleteMixin, Base):
    __tablename__ = "folders"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[int | None] = fk_cascade("folders.id", nullable=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=LEVEL_SUBJECT)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 自引用：删除父节点时级联删除子节点（DB 层 ON DELETE CASCADE）
    children: Mapped[list["Folder"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="Folder.sort_order, Folder.id",
    )
    parent: Mapped["Folder | None"] = relationship(
        back_populates="children", remote_side="Folder.id"
    )

    # 题目用软删除，这里不设 ORM delete 级联，避免误触发物理删除。
    #
    # `passive_deletes=True` 是必须的：没有它时，只要 Folder 变化（哪怕只是
    # 被软删除打时间戳），SQLAlchemy 的 unit-of-work 就会去"同步"这个集合，
    # 对已载入的题目生成 `UPDATE questions SET folder_id = NULL` ——
    # 而该列是 NOT NULL，会让**紧随其后的任意 commit** 抛 IntegrityError。
    # 我们软删除题目走的是批量 UPDATE（见 folder_service.delete_folder），
    # 不需要 ORM 帮忙维护外键。
    questions: Mapped[list["Question"]] = relationship(
        back_populates="folder", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("level IN (1, 2)", name="ck_folders_level"),
        # 同一父下不重名（只约束未软删除的记录，用 SQLite 部分索引）
        Index(
            "uq_folders_parent_name_active",
            "parent_id",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_folders_level_sort", "level", "sort_order"),
    )
