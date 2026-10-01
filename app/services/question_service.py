"""题目业务逻辑（requirements.md 2.3 / 2.5 / 2.13 / 2.14 / 4.2 / 5.5）。

要点：
  - 题干与答案都可以为空（纯图片题目）
  - 题目只挂二级文件夹（由 folder_service 校验）
  - 标签归一化后去重，已存在复用、不存在新建（tag_service）
  - 创建时自动生成首条 review_record：interval_index=0，
    next_review_at = now() + 首个间隔（默认 3 天）—— 需求 2.10
  - 多标签筛选：AND 用 HAVING COUNT(DISTINCT) 实现；OR 用 IN
  - 删除是软删除（deleted_at），不清物理文件（图片删除是后台异步任务）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    MASTERY_MASTERED,
    MASTERY_STILL_WRONG,
    Folder,
    Question,
    QuestionImage,
    ReviewRecord,
    Tag,
    question_tags,
)
from ..models.base import utcnow
from . import folder_service, image_service, settings_service, tag_service


class QuestionError(Exception):
    """题目相关业务异常基类。"""


class QuestionNotFoundError(QuestionError):
    """题目不存在（或已软删除）。"""


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------


@dataclass
class QuestionFilters:
    """列表筛选条件（requirements.md 2.5 / 4.2）。"""

    folder_id: int | None = None
    tags: list[str] = field(default_factory=list)
    # AND = 必须同时含全部标签；OR = 含任一即可
    tag_mode: str = "and"
    keyword: str | None = None
    starred: bool | None = None
    mastery: str | None = None

    def normalized_tags(self) -> list[str]:
        """标签查询词也要归一化，否则全角输入搜不到已存的小写标签。"""
        return tag_service.normalize_tags(self.tags)


def _base_query() -> Select:
    """题目查询骨架：排除软删除，并预加载标签与图片避免 N+1。"""
    return (
        select(Question)
        .where(Question.deleted_at.is_(None))
        .options(selectinload(Question.tags), selectinload(Question.images))
    )


def _apply_filters(stmt: Select, filters: QuestionFilters) -> Select:
    if filters.folder_id is not None:
        stmt = stmt.where(Question.folder_id == filters.folder_id)

    if filters.starred is not None:
        stmt = stmt.where(Question.is_starred.is_(filters.starred))

    if filters.mastery:
        stmt = stmt.where(Question.mastery_status == filters.mastery)

    if filters.keyword:
        like = f"%{filters.keyword.strip()}%"
        # 题干或答案任意命中即可
        stmt = stmt.where(or_(Question.stem.like(like), Question.answer.like(like)))

    tags = filters.normalized_tags()
    if tags:
        if filters.tag_mode == "or":
            # 含任一标签
            sub = (
                select(question_tags.c.question_id)
                .join(Tag, Tag.id == question_tags.c.tag_id)
                .where(Tag.name.in_(tags))
            )
            stmt = stmt.where(Question.id.in_(sub))
        else:
            # 必须同时含全部标签：先筛出候选，再用 COUNT(DISTINCT) 卡数量
            # （requirements.md 5.5）
            sub = (
                select(question_tags.c.question_id)
                .join(Tag, Tag.id == question_tags.c.tag_id)
                .where(Tag.name.in_(tags))
                .group_by(question_tags.c.question_id)
                .having(func.count(func.distinct(Tag.name)) == len(tags))
            )
            stmt = stmt.where(Question.id.in_(sub))

    return stmt


def _attach_folder_names(db: Session, questions: list[Question]) -> None:
    """给题目对象挂上 folder_name（列表响应需要，避免逐条查询）。"""
    folder_ids = {q.folder_id for q in questions}
    if not folder_ids:
        return
    names = dict(
        db.execute(
            select(Folder.id, Folder.name).where(Folder.id.in_(folder_ids))
        ).all()
    )
    for q in questions:
        q.folder_name = names.get(q.folder_id)  # type: ignore[attr-defined]


def list_questions(db: Session, filters: QuestionFilters) -> list[Question]:
    """按条件列出题目，按 created_at 倒序（最新在前）。"""
    stmt = _apply_filters(_base_query(), filters)
    stmt = stmt.order_by(Question.created_at.desc(), Question.id.desc())
    questions = list(db.scalars(stmt).all())
    _attach_folder_names(db, questions)
    return questions


def get_question(db: Session, question_id: int) -> Question:
    """按 id 取未删除题目（含标签与图片）。"""
    stmt = _base_query().where(Question.id == question_id)
    question = db.scalars(stmt).one_or_none()
    if question is None:
        raise QuestionNotFoundError(f"题目 {question_id} 不存在")
    _attach_folder_names(db, [question])
    return question


def current_review_record(db: Session, question_id: int) -> ReviewRecord | None:
    """当前生效的复习记录（未软删除的最新一条）。

    打勾会产生多条记录，撤销是软删除最近一条，所以"当前状态"要按
    created_at/id 取最新的未删除记录，而不是简单取第一条。
    """
    return db.scalars(
        select(ReviewRecord)
        .where(ReviewRecord.question_id == question_id, ReviewRecord.deleted_at.is_(None))
        .order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc())
        .limit(1)
    ).one_or_none()


def attach_review_state(db: Session, questions: list[Question]) -> None:
    """给题目挂上 next_review_at / interval_index / review_count（列表与详情共用）。

    打勾会产生多条记录、撤销是软删除最近一条，所以"当前状态"必须取
    未删除记录里最新的一条。用 row_number() 一次算出全部题目的最新记录，
    避免逐题查询（列表页会有 N+1）。
    """
    if not questions:
        return
    ids = [q.id for q in questions]

    ranked = (
        select(
            ReviewRecord.question_id.label("qid"),
            ReviewRecord.next_review_at.label("next_at"),
            ReviewRecord.interval_index.label("idx"),
            ReviewRecord.review_count.label("cnt"),
            func.row_number()
            .over(
                partition_by=ReviewRecord.question_id,
                order_by=(ReviewRecord.created_at.desc(), ReviewRecord.id.desc()),
            )
            .label("rn"),
        )
        .where(
            ReviewRecord.question_id.in_(ids),
            ReviewRecord.deleted_at.is_(None),
        )
        .subquery()
    )
    rows = db.execute(select(ranked).where(ranked.c.rn == 1)).all()
    latest = {r.qid: r for r in rows}
    for q in questions:
        row = latest.get(q.id)
        q.next_review_at = row.next_at if row else None  # type: ignore[attr-defined]
        q.interval_index = row.idx if row else None  # type: ignore[attr-defined]
        q.review_count = row.cnt if row else None  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# 图片
# --------------------------------------------------------------------------


def _image_metadata(path: str) -> tuple[int | None, int | None, int | None]:
    """读取图片宽高与字节数。

    images 由前端以 URL 数组传入（如 /uploads/2026/10/01/x.jpg），
    路径解析统一交给 image_service，避免两处各写一套相对/绝对路径逻辑。
    读不到就留空，不影响建题。
    """
    return image_service.read_image_metadata(path)


def _sync_images(db: Session, question: Question, urls: list[str]) -> None:
    """按传入的 URL 数组重建该题图片列表（保序去重）。"""
    existing = {img.file_path: img for img in question.images}
    used: set[str] = set()
    result: list[QuestionImage] = []

    for index, raw in enumerate(urls):
        if raw is None:
            continue
        path = str(raw).strip()
        if not path or path in used:
            continue
        used.add(path)

        image = existing.get(path)
        if image is None:
            width, height, size = _image_metadata(path)
            image = QuestionImage(
                question_id=question.id,
                file_path=path,
                width=width,
                height=height,
                size=size,
                sort_order=index,
            )
            db.add(image)
        else:
            image.sort_order = index
        result.append(image)

    # 不在新列表里的旧图片做物理删除（DB 行删除，文件由后台任务处理）
    for path, image in existing.items():
        if path not in used:
            db.delete(image)

    question.images = result  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------


def create_question(
    db: Session,
    *,
    folder_id: int,
    stem: str | None = None,
    answer: str | None = None,
    tags: list[str] | None = None,
    is_starred: bool = False,
    mastery_status: str = MASTERY_STILL_WRONG,
    sort_order: int = 0,
    images: list[str] | None = None,
) -> Question:
    """新建题目，并生成首条 review_record。"""
    # 先校验文件夹（挂错层级要尽早失败），再解析标签
    folder_service.get_category_for_question(db, folder_id)

    # 标签先整体归一化：非法标签应在建题前就报错，而不是建到一半
    tag_objects = tag_service.get_or_create_tags(db, tags or [])

    question = Question(
        folder_id=folder_id,
        stem=stem,
        answer=answer,
        is_starred=is_starred,
        mastery_status=mastery_status or MASTERY_STILL_WRONG,
        sort_order=sort_order,
    )
    db.add(question)
    db.flush()  # 拿到 question.id

    question.tags = tag_objects  # type: ignore[assignment]
    _sync_images(db, question, images or [])

    # 首条复习记录：interval_index=0，next_review_at = now() + intervals[0]
    db.add(
        ReviewRecord(
            question_id=question.id,
            review_count=0,
            interval_index=0,
            last_review_at=None,
            next_review_at=settings_service.first_review_at(db),
            mastery_level=0,
        )
    )

    db.commit()
    db.refresh(question)
    return get_question(db, question.id)


def update_question(
    db: Session,
    question_id: int,
    *,
    folder_id: int | None = None,
    stem: str | None = None,
    answer: str | None = None,
    tags: list[str] | None = None,
    is_starred: bool | None = None,
    mastery_status: str | None = None,
    sort_order: int | None = None,
    images: list[str] | None = None,
) -> Question:
    """编辑题目。只改传入的字段；tags/images 传了则整体替换。"""
    question = get_question(db, question_id)

    if folder_id is not None and folder_id != question.folder_id:
        folder_service.get_category_for_question(db, folder_id)
        question.folder_id = folder_id

    if stem is not None:
        question.stem = stem
    if answer is not None:
        question.answer = answer
    if is_starred is not None:
        question.is_starred = is_starred
    if mastery_status is not None:
        question.mastery_status = mastery_status
    if sort_order is not None:
        question.sort_order = sort_order

    if tags is not None:
        question.tags = tag_service.get_or_create_tags(db, tags)  # type: ignore[assignment]

    if images is not None:
        _sync_images(db, question, images)

    db.commit()
    db.refresh(question)
    return get_question(db, question.id)


def delete_question(db: Session, question_id: int) -> None:
    """软删除题目。

    图片物理文件不在这里删 —— 需求 2.3 要求放后台异步任务。
    """
    question = get_question(db, question_id)
    question.deleted_at = utcnow()
    db.commit()


def detach_image(db: Session, stored_path: str) -> int:
    """把某个图片路径从所有题目的 images 列表里解绑，返回受影响题目数。

    DELETE /upload/image 用：前端只持有 URL，删图片时要先把引用摘干净，
    否则题目的 images 里会留下指向已删文件的死链。
    路径比较用归一化后的形式，避免 `/uploads/a.jpg` 与 `uploads/a.jpg`
    被当成两张不同的图片。
    """
    target = image_service.url_for(stored_path)
    affected = 0
    questions = db.scalars(
        select(Question).where(Question.deleted_at.is_(None))
        .options(selectinload(Question.images))
    ).all()
    for question in questions:
        keep = [
            img for img in question.images
            if image_service.url_for(img.file_path) != target
        ]
        if len(keep) != len(question.images):
            for img in question.images:
                if image_service.url_for(img.file_path) == target:
                    db.delete(img)
            question.images = keep  # type: ignore[assignment]
            affected += 1
    if affected:
        db.commit()
    return affected


def set_starred(db: Session, question_id: int, starred: bool) -> Question:
    """标记/取消重点（requirements.md 2.13）。"""
    question = get_question(db, question_id)
    question.is_starred = starred
    db.commit()
    db.refresh(question)
    return get_question(db, question.id)


def set_mastery(db: Session, question_id: int, mastery_status: str) -> Question:
    """切换正误状态（requirements.md 2.14）。"""
    if mastery_status not in (MASTERY_STILL_WRONG, MASTERY_MASTERED):
        raise QuestionError(f"非法的 mastery_status：{mastery_status}")
    question = get_question(db, question_id)
    question.mastery_status = mastery_status
    db.commit()
    db.refresh(question)
    return get_question(db, question.id)


def toggle_mastery(db: Session, question_id: int) -> Question:
    """在两个状态之间切换（不确定目标状态时用）。"""
    question = get_question(db, question_id)
    target = (
        MASTERY_MASTERED
        if question.mastery_status == MASTERY_STILL_WRONG
        else MASTERY_STILL_WRONG
    )
    return set_mastery(db, question_id, target)
