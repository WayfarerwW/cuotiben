"""标签业务逻辑（requirements.md 2.4 / 5.4，AGENTS.md 4.6）。

核心是**归一化**：入库前 trim + 小写 + 全半角转换，避免「极限」和「极限 」
或「ＡＢＣ」和「abc」变成两个标签。归一化后已存在则复用，不存在才新建。

归一化必须先于唯一性判断，否则 tags.name 的 UNIQUE 约束会挡下重复插入，
而应用层却以为该标签不存在。
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Question, Tag, question_tags

MAX_TAG_LENGTH = 100


class TagError(Exception):
    """标签相关业务异常基类。"""


class InvalidTagError(TagError):
    """标签为空或超长。"""


def normalize_tag(name: str) -> str:
    """归一化标签名：trim + 全角转半角 + 小写。

    步骤（顺序有讲究）：
      1. NFKC 归一化：把全角字母数字、全角空格、各种兼容字符折叠成常规形式
         （ＡＢＣ -> ABC，"　" -> " "）。这一步也能顺手处理一部分繁体/兼容字。
      2. 逐字符补一道 ASCII 全角转半角：NFKC 对全角标点（！＂＃…～）处理不完整，
         统一映射 [U+FF01, U+FF5E] -> [U+0021, U+007E]。
      3. 取首尾空白（全角空格已在第 1 步变成半角空格）。
      4. 小写化（用 casefold 而非 lower，能多处理一些语言的大小写）。

    例：
        "  极限  "        -> "极限"
        "极限　"          -> "极限"          （全角空格）
        "ＡＢＣ"          -> "abc"           （全角字母）
        "ＬＩＭＩＴ"      -> "limit"
        "Limit!"          -> "limit!"        （标点保留，不做语义清洗）
    """
    text = unicodedata.normalize("NFKC", name)

    converted: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            converted.append(chr(code - 0xFEE0))
        else:
            converted.append(ch)
    text = "".join(converted)

    return text.strip().casefold()


def normalize_tags(names: list[str] | None) -> list[str]:
    """批量归一化并按首次出现顺序去重。

    去重放在归一化之后：["极限", " 极限 ", "极限　"] 三个输入只会保留一个。
    返回顺序保持输入顺序，便于前端展示稳定。
    """
    if not names:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        if raw is None:
            continue
        normalized = normalize_tag(str(raw))
        if not normalized:
            # 纯空白标签直接忽略，不报错（前端回车可能产生空项）
            continue
        if len(normalized) > MAX_TAG_LENGTH:
            raise InvalidTagError(f"标签过长（上限 {MAX_TAG_LENGTH} 字）：{normalized[:20]}…")
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def get_or_create_tags(db: Session, names: list[str]) -> list[Tag]:
    """把归一化后的标签名解析成 Tag 对象，不存在则新建。

    先整体归一化去重，再一次性查询已有标签，避免逐个 SELECT。
    注意：这里不 commit，由调用方（service）控制事务边界。
    """
    normalized = normalize_tags(names)
    if not normalized:
        return []

    existing = {
        t.name: t
        for t in db.scalars(select(Tag).where(Tag.name.in_(normalized))).all()
    }

    result: list[Tag] = []
    for name in normalized:
        tag = existing.get(name)
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            existing[name] = tag
        result.append(tag)
    return result


def list_tags(db: Session) -> list[Tag]:
    """全部标签（requirements.md 4.3 GET /tags）。"""
    return list(db.scalars(select(Tag).order_by(Tag.name)).all())


def list_tags_with_counts(db: Session) -> list[tuple[Tag, int]]:
    """标签 + 使用次数（只统计未删除题目），按使用次数倒序、名称升序。

    联想补全和标签云都需要这个顺序：常用的排前面。

    用子查询先算出每个标签的有效题目数，再 LEFT JOIN 回标签表 ——
    如果直接在 question_tags 上再 join questions，被软删除的题目仍会留在
    外侧，count 会把它们算进去。
    """
    counts = (
        select(
            question_tags.c.tag_id.label("tag_id"),
            func.count(question_tags.c.question_id).label("cnt"),
        )
        .join(Question, Question.id == question_tags.c.question_id)
        .where(Question.deleted_at.is_(None))
        .group_by(question_tags.c.tag_id)
        .subquery()
    )

    rows = db.execute(
        select(Tag, func.coalesce(counts.c.cnt, 0))
        .outerjoin(counts, counts.c.tag_id == Tag.id)
        .order_by(func.coalesce(counts.c.cnt, 0).desc(), Tag.name)
    ).all()
    return [(tag, int(count)) for tag, count in rows]


def search_tags(db: Session, keyword: str, limit: int = 20) -> list[Tag]:
    """标签联想（requirements.md 4.3 GET /tags/search?q=）。

    查询词也要归一化，否则输入全角「ＡＢＣ」搜不到已存的「abc」。
    """
    normalized = normalize_tag(keyword)
    if not normalized:
        return []
    stmt = (
        select(Tag)
        .where(Tag.name.like(f"%{normalized}%"))
        .order_by(Tag.name)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())
