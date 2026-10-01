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

from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    IMAGE_KIND_STEM,
    IMAGE_KINDS,
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
from . import folder_service, image_service, review_service, tag_service


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
    #: 只取这些 id（导出"手动勾选"范围用）。空列表表示不按 id 过滤。
    ids: list[int] | None = None

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
    if filters.ids is not None:
        if not filters.ids:
            # 空列表 = 明确"一个都不要"，而不是"不过滤"
            return stmt.where(false())
        stmt = stmt.where(Question.id.in_(filters.ids))

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


def register_image(
    db: Session,
    *,
    file_path: str,
    width: int | None = None,
    height: int | None = None,
    size: int | None = None,
    sort_order: int = 0,
    kind: str = IMAGE_KIND_STEM,
) -> QuestionImage:
    """为刚上传的图片落一条 question_images 记录，返回该记录。

    DELETE /upload/image/{id} 需要 question_images 主键，所以上传时就先建行
    （此时还没有 question_id）。前端录题时把 url+kind 放进 questions.images，
    _sync_images 会按 (路径, 位置) 找到这一行并复用，不会产生重复记录。

    `kind` 允许在上传时就指定位置；若不指定则默认题干图，
    录题时 _sync_images 会按最终传入的 kind 覆盖它（同一张图换位置是合法的）。

    这里不 commit，由调用方控制事务（上传接口自己 commit）。
    """
    image = QuestionImage(
        question_id=None,  # 尚未挂到题目上
        file_path=file_path,
        width=width,
        height=height,
        size=size,
        sort_order=sort_order,
        kind=kind if kind in IMAGE_KINDS else IMAGE_KIND_STEM,
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return image


def _normalize_image_key(raw: str) -> str:
    """把 URL 或路径统一成库内相对路径，作为图片的比对键。

    前端可能传 `/uploads/2026/10/01/x.jpg`（上传接口返回的 url），
    也可能传 `uploads/2026/10/01/x.jpg`（file_path），两者是同一张图。
    """
    return image_service.absolute_path_of(raw).relative_to(
        image_service.PROJECT_ROOT
    ).as_posix()


#: 同一张图不能既当题干图又当答案图？其实可以（同一张图两面都用是合理的），
#: 所以去重键是 (路径, 位置) 而不是只有路径。见 _sync_images。
ImageRef = tuple[str, str]


def _image_in_refs(ref: object) -> ImageRef | None:
    """把入参归一化成 (路径, 位置)。兼容旧的裸字符串写法（当作题干图）。"""
    if ref is None:
        return None
    if isinstance(ref, str):
        kind = IMAGE_KIND_STEM
        raw = ref
    elif isinstance(ref, dict):
        raw = ref.get("url") or ref.get("file_path") or ""
        kind = ref.get("kind") or IMAGE_KIND_STEM
    else:
        # pydantic 模型（QuestionImageIn）
        raw = getattr(ref, "url", "") or getattr(ref, "file_path", "")
        kind = getattr(ref, "kind", None) or IMAGE_KIND_STEM

    text = str(raw).strip()
    if not text:
        return None
    key = _normalize_image_key(text)
    if not key:
        return None
    if kind not in IMAGE_KINDS:
        kind = IMAGE_KIND_STEM
    return (key, str(kind))


def _sync_images(db: Session, question: Question, refs: list) -> None:
    """按传入的图片列表重建该题图片（保序去重）。

    去重键是 **(路径, 位置)**：同一张图可以既作题干图又作答案图，
    按路径去重会把其中一面悄悄丢掉。

    排序在每个位置内**各自从 0 开始**，这样题干图的顺序不会因为
    答案图的存在而跳号（复习视图与 PDF 各自按 sort_order 渲染）。

    优先复用已存在的 question_images 行：
      - 本题已挂的（编辑场景）
      - 上传接口刚建好、还没挂题的孤儿行（register_image 产生）
    """
    existing: dict[ImageRef, QuestionImage] = {
        (_normalize_image_key(img.file_path), img.kind or IMAGE_KIND_STEM): img
        for img in question.images
    }
    used: set[ImageRef] = set()
    result: list[QuestionImage] = []
    counters: dict[str, int] = {}

    for raw in refs:
        ref = _image_in_refs(raw)
        if ref is None or ref in used:
            continue
        used.add(ref)
        path, kind = ref
        order = counters.get(kind, 0)
        counters[kind] = order + 1

        image = existing.get(ref)
        if image is None:
            # 找上传时先建好的孤儿行（同一路径、尚未挂题），复用它而不是再插一条
            image = db.scalars(
                select(QuestionImage).where(
                    QuestionImage.question_id.is_(None),
                    QuestionImage.file_path == path,
                )
            ).first()
        if image is None:
            width, height, size = _image_metadata(path)
            image = QuestionImage(
                question_id=question.id,
                file_path=path,
                width=width,
                height=height,
                size=size,
                sort_order=order,
                kind=kind,
            )
            db.add(image)
        else:
            image.question_id = question.id
            image.sort_order = order
            image.kind = kind
        result.append(image)

    # 不在新列表里的旧图片只解除本题关联，不删记录也不删文件：
    # 那条记录可能被上传接口返回的 id 引用着，也可能还有别的题在用。
    # 真正删除走 DELETE /upload/image/{id}。
    for ref, image in existing.items():
        if ref not in used:
            image.question_id = None

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
    # 生成逻辑收敛在 review_service，避免两处各写一遍记忆曲线起点
    review_service.create_initial_record(db, question.id)

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
    fields_to_update: set[str] | None = None,
) -> Question:
    """编辑题目。

    传入 `fields_to_update`（路由用 `set(payload.model_fields_set)`）时按
    "字段是否出现过"决定改不改，从而区分"未传"与"传 null"：

      - 未传            -> 保持原值
      - `stem`/`answer` 传 null -> 清空（需求 2.3 允许纯图片题目）
      - `tags`/`images` 传 [] 或 null -> 清空（tags 清空标签、images 解除全部图片关联）
      - 其余字段（folder_id / is_starred / mastery_status / sort_order）
        不允许为 null，由 schemas.QuestionUpdate 的校验器直接 422，不会走到这里

    不传 `fields_to_update` 时退化为旧语义（非 None 才更新），兼容已有调用。
    """
    question = get_question(db, question_id)
    explicit = fields_to_update is not None

    def should(field: str, value: object) -> bool:
        if explicit:
            return field in fields_to_update
        return value is not None

    if should("folder_id", folder_id):
        if folder_id is None:
            raise QuestionError("folder_id 不允许为 null")
        if folder_id != question.folder_id:
            folder_service.get_category_for_question(db, folder_id)
            question.folder_id = folder_id

    if should("stem", stem):
        question.stem = stem
    if should("answer", answer):
        question.answer = answer

    if should("is_starred", is_starred):
        if is_starred is None:
            raise QuestionError("is_starred 不允许为 null")
        question.is_starred = is_starred

    if should("mastery_status", mastery_status):
        if mastery_status is None:
            raise QuestionError("mastery_status 不允许为 null")
        question.mastery_status = mastery_status

    if should("sort_order", sort_order):
        if sort_order is None:
            raise QuestionError("sort_order 不允许为 null")
        question.sort_order = sort_order

    # tags / images 传了就整体替换；传 [] 或 null 都是"清空"
    if should("tags", tags):
        question.tags = tag_service.get_or_create_tags(db, tags or [])  # type: ignore[assignment]

    if should("images", images):
        _sync_images(db, question, images or [])

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


class QuestionImageNotFoundError(QuestionError):
    """图片不存在（或已删除）。"""


def delete_image(db: Session, image_id: int) -> dict:
    """按 question_images 主键删除图片（requirements.md 4.4 DELETE /upload/image/{id}）。

    步骤：
      1. 取出该图片记录（拿到 file_path）
      2. 把它从各个题目的 images 关联里摘掉，避免留下指向已删文件的死链
      3. 删除物理文件（不进后台队列：本地单机 unlink 一个小文件是毫秒级，
         引入队列的复杂度大于收益；量级变大再换 BackgroundTasks）
      4. 删除数据库记录

    返回 {image_id, file_path, unbound_questions, file_deleted} 供 router 组装响应。
    """
    image = db.get(QuestionImage, image_id)
    if image is None:
        raise QuestionImageNotFoundError(f"图片 {image_id} 不存在")

    stored_path = image.file_path
    target_url = image_service.url_for(stored_path)

    # 2. 解绑：把引用了同一路径的关联行一并清掉
    unbound = 0
    others = db.scalars(
        select(QuestionImage).where(QuestionImage.id != image_id)
        .options(selectinload(QuestionImage.question))
    ).all()
    for other in others:
        if image_service.url_for(other.file_path) != target_url:
            continue
        question = other.question
        if question is not None:
            question.images = [
                img for img in question.images if img.id != other.id
            ]  # type: ignore[assignment]
            unbound += 1
        db.delete(other)

    # 3. 物理文件
    file_deleted = image_service.delete_file(stored_path)

    # 4. 记录本身
    db.delete(image)
    db.commit()

    return {
        "image_id": image_id,
        "file_path": stored_path,
        "unbound_questions": unbound,
        "file_deleted": file_deleted,
    }


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
