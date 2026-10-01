"""设置路由（requirements.md 4.8）。

只做参数校验与响应组装，业务逻辑在 services/settings_service.py。
路径形态：/settings 单复数同形，保持现状（AGENTS.md 3.5）。

关于"首次启动写入默认值"：不在本模块做，而是由 main.py 的 lifespan 在
启动时调用 settings_service.ensure_default_settings —— 那样无论是否访问过
本接口，配置都已就位；而且启动路径也会覆盖"库被清空/只存了部分 key"的情况
（见 settings_service.ensure_default_settings：只补缺失，不覆盖已有）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import SettingsOut, SettingsUpdate
from ..services import settings_service
from ..services.settings_service import SettingsError

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsOut, summary="获取全部配置")
def get_settings(db: Session = Depends(get_db)) -> SettingsOut:
    """返回 intervals / backfill_limit / backfill_reset_days。

    库中缺失的 key 回退到默认值，不会返回 null。
    """
    return SettingsOut(**settings_service.get_all_settings(db))


@router.put("", response_model=SettingsOut, summary="批量更新配置")
def update_settings(
    payload: SettingsUpdate, db: Session = Depends(get_db)
) -> SettingsOut:
    """只更新请求体里出现的字段。

    传 `null` 的 key 表示**删除该配置项、回退默认值**；
    未出现的字段保持不变。"未传"与"传 null"由 `model_fields_set` 区分。

    校验（与 Pydantic 层一致，service 再兜一道，因为写库路径可能不止 API 一条）：
      - `intervals` 必须是 4 个正整数且**严格递增**（requirements.md 2.10）
      - `backfill_limit` >= 0
      - `backfill_reset_days` >= 1
    """
    try:
        settings_service.update_settings(
            db,
            intervals=payload.intervals,
            backfill_limit=payload.backfill_limit,
            backfill_reset_days=payload.backfill_reset_days,
            fields_to_update=set(payload.model_fields_set),
        )
    except SettingsError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return SettingsOut(**settings_service.get_all_settings(db))
