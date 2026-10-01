"""GitHub 自动同步路由（AGENTS.md 五）。

按 AGENTS.md 3.5：`/sync` 是**动作命名空间**（功能入口，返回的不一定是
同名实体），所以保持单数，不要改成 `/syncs` —— 与 `/review`、`/upload`、
`/export`、`/data` 一致。

  GET  /sync/status   当前配置与运行状态
  POST /sync/now      立即同步一次

本层只做参数校验与响应组装，业务在 sync_service（AGENTS.md 3.4）。
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from ..schemas import SyncRunOut, SyncStatusOut
from ..services import sync_service
from ..services.sync_service import NotConfiguredError, SyncError

router = APIRouter(prefix="/sync", tags=["sync"])


@contextmanager
def _service_errors():
    """把 service 异常翻成 HTTP 状态码（模式同其它 router）。"""
    try:
        yield
    except NotConfiguredError as exc:
        # 未启用/未配置属于"当前状态不允许这个操作"，用 409 更准确：
        # 请求本身没问题，是服务端状态不满足。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/status", response_model=SyncStatusOut, summary="同步状态")
def get_status() -> SyncStatusOut:
    """配置与运行状态。

    未启用时也返回 200（这是"查询状态"接口，不是"执行"接口），
    由 `enabled` / `ready` 字段表达能不能同步。
    """
    return SyncStatusOut(**sync_service.status_report())


@router.post("/now", response_model=SyncRunOut, summary="立即同步一次")
async def sync_now_endpoint() -> SyncRunOut:
    """立即执行一次 add + commit + push。

    service 里对同步做了串行化：若已有同步在进行，返回 `status="busy"`
    而不是排队等待 —— 排队会让这个接口在慢网络下长时间挂住。

    放进线程池：内部要起 git 子进程并走网络，直接在 async 里做会
    阻塞事件循环（同 PDF 导出的理由）。
    """
    with _service_errors():
        result = await run_in_threadpool(sync_service.sync_now, reason="manual")
    return SyncRunOut(**result)
