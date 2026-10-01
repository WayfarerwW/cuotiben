"""标签路由（requirements.md 4.3）。

只做参数校验与响应组装，业务逻辑在 services/tag_service.py。
路径形态：/tags 是资源集合，用复数（AGENTS.md 3.5）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import TagWithCount
from ..services import tag_service

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagWithCount], summary="全部标签（按使用次数倒序）")
def list_tags(db: Session = Depends(get_db)) -> list[TagWithCount]:
    """返回全部标签及其使用次数。

    顺序为"使用次数倒序 + 名称升序"：
      - 标签选择题、标签云（ui-design 4.5「词云：高频标签」）都要高频在前
      - question_count 只统计未删除的题目，避免已删题目把计数撑高
    """
    return [
        TagWithCount(id=tag.id, name=tag.name, created_at=tag.created_at,
                     question_count=count)
        for tag, count in tag_service.list_tags_with_counts(db)
    ]


@router.get("/search", response_model=list[TagWithCount], summary="标签联想（模糊匹配）")
def search_tags(
    q: str = Query("", description="关键词，支持全半角/大小写混输"),
    limit: int = Query(20, ge=1, le=100, description="最多返回条数"),
    db: Session = Depends(get_db),
) -> list[TagWithCount]:
    """按关键词模糊匹配标签名，用于输入框下拉联想（requirements.md 2.4）。

    查询词先做与入库相同的归一化（trim + 小写 + 全半角），
    否则输入全角「ＡＢＣ」搜不到已存的「abc」。
    """
    matched = tag_service.search_tags(db, q, limit=limit)
    if not matched:
        return []
    # 复用一次统计查询取计数：标签总量不大，比逐条 count 更划算
    counts = {
        tag.id: count for tag, count in tag_service.list_tags_with_counts(db)
    }
    return [
        TagWithCount(id=tag.id, name=tag.name, created_at=tag.created_at,
                     question_count=counts.get(tag.id, 0))
        for tag in matched
    ]
