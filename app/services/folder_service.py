"""文件夹业务逻辑（requirements.md 2.2 / 4.1）。

两级结构：level=1 是学科，level=2 是大类；题目只挂在大类下。

分层约定（AGENTS.md 3.4）：
  - 本模块承载业务规则与数据库操作，是唯一写入入口
  - 只接收外部传入的 Session，不自己开会话、不 commit 之外不碰事务边界以外的事
  - 抛业务异常（下方 *Error），不抛 HTTPException —— 与 Web 框架解耦，
    由 routers/folders.py 转成对应状态码
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import LEVEL_CATEGORY, LEVEL_SUBJECT, Folder, Question
from ..models.base import utcnow

# --------------------------------------------------------------------------
# 业务异常
# --------------------------------------------------------------------------


class FolderError(Exception):
    """文件夹相关业务异常基类。"""


class FolderNotFoundError(FolderError):
    """指定的文件夹不存在（或已被软删除）。"""


class DuplicateFolderNameError(FolderError):
    """同一父目录下已存在同名文件夹。"""


class InvalidFolderStructureError(FolderError):
    """违反两级结构：层级不对、父节点不存在、要删的还有内容等。"""


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------


def get_folder(db: Session, folder_id: int) -> Folder:
    """按 id 取未删除的文件夹，取不到抛 FolderNotFoundError。"""
    folder = db.scalars(
        select(Folder).where(Folder.id == folder_id, Folder.deleted_at.is_(None))
    ).one_or_none()
    if folder is None:
        raise FolderNotFoundError(f"文件夹 {folder_id} 不存在")
    return folder


def _question_counts(db: Session) -> dict[int, int]:
    """各文件夹下未删除题目的数量。"""
    rows = db.execute(
        select(Question.folder_id, func.count(Question.id))
        .where(Question.deleted_at.is_(None))
        .group_by(Question.folder_id)
    ).all()
    return {folder_id: count for folder_id, count in rows}


def list_tree(db: Session) -> list[Folder]:
    """完整两级树（requirements.md 4.1 GET /folders/tree）。

    返回顶层学科列表，每个学科的 children 是大类。
    被软删除的文件夹不出现；question_count 只统计未删除的题目。
    """
    folders = list(
        db.scalars(
            select(Folder)
            .where(Folder.deleted_at.is_(None))
            .order_by(Folder.sort_order, Folder.id)
        ).all()
    )
    counts = _question_counts(db)

    children: dict[int | None, list[Folder]] = {}
    for f in folders:
        children.setdefault(f.parent_id, []).append(f)

    for f in folders:
        kids = children.get(f.id, [])
        # 学科节点的计数 = 自身题目 + 各子类题目，便于前端在折叠状态显示总量
        f.children = kids  # type: ignore[assignment]
        f.question_count = counts.get(f.id, 0) + sum(  # type: ignore[attr-defined]
            counts.get(k.id, 0) for k in kids
        )

    return children.get(None, [])


def list_subjects(db: Session) -> list[Folder]:
    """只取一级学科（不带树）。"""
    return list(
        db.scalars(
            select(Folder)
            .where(Folder.deleted_at.is_(None), Folder.level == LEVEL_SUBJECT)
            .order_by(Folder.sort_order, Folder.id)
        ).all()
    )


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    """文件夹名去首尾空白。空白名在上面已被 Pydantic 拦掉。"""
    return name.strip()


def _assert_unique_name(
    db: Session, name: str, parent_id: int | None, *, exclude_id: int | None = None
) -> None:
    """同一父下不允许同名（只比较未软删除的）。

    注意 parent_id 可能为 NULL，SQL 里 `= NULL` 永远不成立，
    必须分别用 is_(None) 与 == 处理。
    """
    stmt = select(Folder.id).where(Folder.deleted_at.is_(None), Folder.name == name)
    stmt = stmt.where(Folder.parent_id.is_(None) if parent_id is None
                      else Folder.parent_id == parent_id)
    if exclude_id is not None:
        stmt = stmt.where(Folder.id != exclude_id)
    if db.scalars(stmt).first() is not None:
        where = "顶级" if parent_id is None else f"父文件夹 {parent_id} 下"
        raise DuplicateFolderNameError(f"{where}已存在同名文件夹「{name}」")


def _resolve_level(db: Session, parent_id: int | None) -> int:
    """根据 parent_id 推导层级，并校验两级结构。"""
    if parent_id is None:
        return LEVEL_SUBJECT
    parent = get_folder(db, parent_id)  # 不存在会抛 FolderNotFoundError
    if parent.level != LEVEL_SUBJECT:
        raise InvalidFolderStructureError(
            f"文件夹只支持两级，不能挂在二级文件夹「{parent.name}」下"
        )
    return LEVEL_CATEGORY


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------


def create_folder(
    db: Session, name: str, parent_id: int | None = None, sort_order: int = 0
) -> Folder:
    """新建学科（parent_id=None）或大类（parent_id=学科 id）。"""
    name = _normalize_name(name)
    if not name:
        raise InvalidFolderStructureError("文件夹名不能为空")

    level = _resolve_level(db, parent_id)
    _assert_unique_name(db, name, parent_id)

    folder = Folder(name=name, parent_id=parent_id, level=level, sort_order=sort_order)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return folder


def update_folder(
    db: Session,
    folder_id: int,
    *,
    name: str | None = None,
    sort_order: int | None = None,
) -> Folder:
    """重命名 / 调整排序。只改传入的字段。"""
    folder = get_folder(db, folder_id)

    if name is not None:
        new_name = _normalize_name(name)
        if not new_name:
            raise InvalidFolderStructureError("文件夹名不能为空")
        if new_name != folder.name:
            _assert_unique_name(db, new_name, folder.parent_id, exclude_id=folder.id)
            folder.name = new_name

    if sort_order is not None:
        folder.sort_order = sort_order

    db.commit()
    db.refresh(folder)
    return folder


def _collect_subtree_ids(db: Session, folder: Folder) -> list[Folder]:
    """返回自身 + 未删除的直接子节点（两级结构，够用）。"""
    children = list(
        db.scalars(
            select(Folder).where(Folder.parent_id == folder.id, Folder.deleted_at.is_(None))
        ).all()
    )
    return [folder, *children]


def delete_folder(db: Session, folder_id: int, *, force: bool = False) -> int:
    """软删除文件夹（学科会连带软删除其下大类），返回受影响行数。

    安全设计：若该文件夹（或其子文件夹）下还有未删除的题目，默认拒绝删除，
    避免题目变成挂在已删除文件夹上的孤儿 —— 那会让题目"消失"却仍占着数据。
    确认要删时传 force=True，此时只软删除文件夹本身，题目保持原状不动。
    """
    folder = get_folder(db, folder_id)
    targets = _collect_subtree_ids(db, folder)
    target_ids = [t.id for t in targets]

    active_questions = db.scalar(
        select(func.count(Question.id)).where(
            Question.folder_id.in_(target_ids), Question.deleted_at.is_(None)
        )
    ) or 0

    if active_questions and not force:
        raise InvalidFolderStructureError(
            f"该文件夹（含子文件夹）下还有 {active_questions} 道题，"
            "请先移走或删除这些题目，或使用 force=true 仅删除文件夹"
        )

    now = utcnow()
    for t in targets:
        t.deleted_at = now
    db.commit()
    return len(targets)
