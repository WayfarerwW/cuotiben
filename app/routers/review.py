"""复习路由（requirements.md 4.5）。

只做参数校验与响应组装，业务规则在 services/review_service.py。

路径说明（AGENTS.md 3.5）：
  /review/* 属于**动作命名空间**（功能入口），因此保持单数，不写成 /reviews/*。
  `GET /review/today` 返回的是"待复习题目列表"，不是 review_records 集合，
  写成复数反而会让语义指向错误实体。

路径参数从需求文档的 `{id}` 明确为 `{question_id}`：打勾/撤销操作的是**题目**，
而 review_record 是打勾产生的结果。需求 4.5 原文写作 `/review/{id}/check`，
`{id}` 存在"是题目还是复习记录"的歧义，这里按题目 id 落地并在文档中记录。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import (
    MessageOut,
    ReviewCheckRequest,
    ReviewItemOut,
    ReviewRecordOut,
)
from ..services import review_service
from ..services.review_service import (
    NothingToUndoError,
    ReviewQuestionNotFoundError,
)

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/today", response_model=list[ReviewItemOut], summary="今日待复习队列")
def get_today(db: Session = Depends(get_db)) -> list[ReviewItemOut]:
    """今日队列 = 今日到期 + 最多 N 道逾期补卡。

    "今日"以本地时区 0 点为边界。返回数组（前端契约如此），
    队列构成明细见 review_service.list_today 返回的 meta；
    界面若要显示"今日到期 X 题，补卡 Y 题（上限 N）"，
    用 GET /review/count 与后续的补卡统计接口组合即可。
    """
    items, _meta = review_service.list_today(db)
    return [ReviewItemOut.model_validate(i) for i in items]


@router.get("/count", response_model=dict, summary="待复习数量")
def get_count(db: Session = Depends(get_db)) -> dict:
    """铃铛角标用。返回 {"count": N}（与 list_today 同口径）。"""
    return {"count": review_service.count_due(db)}


@router.post("/{question_id}/check", response_model=ReviewRecordOut,
             summary="打勾（任何题、任何时间都可打）")
def check(
    question_id: int,
    payload: ReviewCheckRequest | None = None,
    db: Session = Depends(get_db),
) -> ReviewRecordOut:
    """打勾。

    不校验是否处于待复习状态；允许重复打勾，**不返回 409**（AGENTS.md 4.1）。
    interval_index 重置为 0，next_review_at = now() + INTERVALS[0]（默认 3 天）。
    """
    mastery = payload.resolved_mastery if payload is not None else None
    try:
        record = review_service.check(db, question_id, mastery=mastery)
    except ReviewQuestionNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return ReviewRecordOut.model_validate(record)


@router.post("/{question_id}/uncheck", response_model=MessageOut, summary="撤销打勾")
def uncheck(question_id: int, db: Session = Depends(get_db)) -> MessageOut:
    """撤销 = 软删除最近一条 review_record，上一轮自动生效（requirements.md 5.3）。"""
    try:
        review_service.uncheck(db, question_id)
    except ReviewQuestionNotFoundError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except NothingToUndoError as e:
        # 没有可撤销的记录属于"当前状态冲突"，用 409 而不是 400
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return MessageOut(message=f"已撤销题目 {question_id} 的最近一次打勾")
