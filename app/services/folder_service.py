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


def get_category_for_question(db: Session, folder_id: int) -> Folder:
    """校验 folder_id 可作为题目挂载点，返回该二级文件夹。

    需求 2.2：题目**只挂在二级文件夹（大类）下**。挂到一级学科上属于结构性
    错误，必须在写入前拦下来，否则会绕过两级约定。
    """
    folder = get_folder(db, folder_id)
    if folder.level != LEVEL_CATEGORY:
        raise InvalidFolderStructureError(
            f"题目只能挂在大类（二级文件夹）下，「{folder.name}」是学科"
        )
    if folder.parent_id is None:
        # 数据被手工改坏时的兜底：level=2 必须有父
        raise InvalidFolderStructureError(
            f"文件夹「{folder.name}」层级数据异常（level=2 但无父节点）"
        )
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


def delete_folder(db: Session, folder_id: int, *, force: bool = False) -> dict:
    """软删除文件夹，**连同其下题目一起软删除**（requirements.md 2.2）。

    两级结构：删学科会连带其下大类；两个层级下的题目都一并软删除。

    ## 安全阀

    若该文件夹（或其子文件夹）下还有未删除的题目，默认（force=False）**拒绝**
    并抛 `InvalidFolderStructureError`（router 转 409）。这是刻意的：
    删除会连带让这些题在界面上消失，必须由用户显式确认。

    ## force=True 的语义（本次变更）

    原来是"仅软删除文件夹，题目保持原状不动"。那个做法留下的是**半死状态**：
    题目仍算"活着"（列表里看得见），却挂在一个已删除的大类下 ——
    侧边栏没有任何大类可点、编辑时「所属大类」下拉是空的（保存必 422），
    而它们的图片既不可见、也不被孤儿扫描识别（孤儿判定只看
    `question.deleted_at`，不看题目所属文件夹是否已删），于是永久留在磁盘上。

    真实发生过：用户把某学科下的大类全删了，导致 4 道活题挂在已删除的大类下、
    4 张图片既看不到也清不掉。

    现在 force=True 表示用户已确认"这一整块都不要了"：
    **连同其下题目一起软删除**。题目一被软删除，它的图片立刻满足孤儿条件，
    现有 `POST /upload/cleanup` 就能回收 —— 所以这里**不需要**同步删物理文件
    （那样既慢又不可逆，而且和多题共享同一图片的场景会互相伤害）。

    返回 {"folders", "questions", "images"} 供 router 组装响应；
    `images` 是"这些题挂了多少张图"，用于提示用户去清理孤儿图片。
    """
    folder = get_folder(db, folder_id)
    targets = _collect_subtree_ids(db, folder)
    target_ids = [t.id for t in targets]

    questions = list(db.scalars(
        select(Question).where(
            Question.folder_id.in_(target_ids), Question.deleted_at.is_(None)
        )
    ).all())

    if questions and not force:
        raise InvalidFolderStructureError(
            f"该文件夹（含子文件夹）下还有 {len(questions)} 道题，"
            "请先移走或删除这些题目；若确认这些题也一并删除，"
            "请使用 force=true"
        )

    # 图片张数必须在软删除题目**之前**数好（之后再查就要按 deleted_at 过滤了）。
    image_count = sum(len(q.images or []) for q in questions)

    now = utcnow()
    # **两张表都用批量 UPDATE，不要把对象改脏。**
    #
    # 为什么必须这样（实测踩过）：先把题目对象读进会话、再逐个改
    # `deleted_at` 时，SQLAlchemy 的 unit-of-work 会在**下一次 flush**
    # 生成 `UPDATE questions SET folder_id = NULL` —— 它把这批题视为与
    # Folder 解除了关联，而 `questions.folder_id` 是 NOT NULL，
    # 于是接着调用 `create_folder()`（或任何 commit）就 IntegrityError。
    # 现象很迷惑：删文件夹本身成功，**之后随便新建个文件夹才崩**。
    #
    # 批量 UPDATE 只发一条语句、不把对象标脏，也就没有这个关联同步；
    # 顺带也省掉了"把成百上千道题加载进会话"的开销。
    if questions:
        db.query(Question).filter(
            Question.id.in_([q.id for q in questions])
        ).update({Question.deleted_at: now}, synchronize_session=False)
    db.query(Folder).filter(
        Folder.id.in_([t.id for t in targets])
    ).update({Folder.deleted_at: now}, synchronize_session=False)
    db.commit()

    return {
        "folders": len(targets),
        "questions": len(questions),
        "images": image_count,
    }
