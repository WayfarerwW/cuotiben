"""数据说明页路由（requirements.md 2.17）。

按 AGENTS.md 3.4：本层只做参数校验与响应组装，业务在 data_service。
按 AGENTS.md 3.5：`/data` 指向一个功能入口（数据管理视图），属动作型命名空间。

三个入口：
  GET  /data/paths    页面展示用的路径与存在性
  POST /data/backup   手动备份：数据库 + uploads/ 复制到 backups/<时间戳>/
  GET  /data/export   导出完整业务数据为 JSON

备份与导出都是**纯本地文件操作**，不联网、不外发数据。
"""

from __future__ import annotations

import urllib.parse
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import BackupResultOut, DataPathsOut
from ..services import data_service
from ..services.data_service import BackupError, NothingToBackupError

router = APIRouter(prefix="/data", tags=["data"])


@contextmanager
def _service_errors():
    """把 service 异常翻成 HTTP 状态码（模式同其它 router）。"""
    try:
        yield
    except NothingToBackupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/paths", response_model=DataPathsOut, summary="数据路径与存在性")
def get_paths() -> DataPathsOut:
    with _service_errors():
        return DataPathsOut(**vars(data_service.data_paths()))


@router.post("/backup", response_model=BackupResultOut, summary="手动备份")
async def create_backup(db: Session = Depends(get_db)) -> BackupResultOut:
    """把数据库与 uploads/ 复制到 backups/<时间戳>/。

    放进线程池：复制整个 uploads/ 可能是几百兆的磁盘 IO，
    直接在 async 里做会阻塞事件循环（同 PDF 导出的理由）。
    """
    with _service_errors():
        result = await run_in_threadpool(data_service.create_backup)
    return BackupResultOut(**vars(result))


@router.get("/export", summary="导出全部业务数据为 JSON")
async def export_json(db: Session = Depends(get_db)) -> Response:
    """返回 JSON 文件流。

    用 `filename*=UTF-8''…` 让中文文件名正确落地（同 PDF 导出）。
    """
    with _service_errors():
        # 查库 + 序列化也放线程池：题量大时这一步不轻
        payload = await run_in_threadpool(data_service.export_json_bytes, db)

    filename = data_service.json_filename()
    quoted = urllib.parse.quote(filename, safe="")
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; filename=\"cuotiben-backup.json\"; "
                f"filename*=UTF-8''{quoted}"
            ),
            "Content-Length": str(len(payload)),
        },
    )
