"""PDF 导出路由（requirements.md 2.16 / 4.7）。

按 AGENTS.md 3.4：本层只做参数校验与响应组装，业务在 export_service。
按 AGENTS.md 3.5：`/export` 是动作命名空间。

**关键点：WeasyPrint 是同步阻塞的。**
渲染一份几十题的 PDF 可能耗时数秒，直接在 `async def` 里调用会卡住事件循环
（连 /health 都不响应）。因此路由声明为 `async def`，把渲染丢进
`run_in_threadpool`。

顺带一个易踩的坑：`run_in_threadpool` 必须包住**整个导出流程**
（查库 + 渲染），不能只包渲染。SQLAlchemy 的同步 Session 本来就会阻塞，
分开包等于还有一半在事件循环里跑。
"""

from __future__ import annotations

import urllib.parse
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ExportPdfRequest
from ..services import export_service
from ..services.export_service import (
    ExportError,
    ExportScopeError,
    ExportUnavailableError,
)

router = APIRouter(tags=["export"])


@contextmanager
def _service_errors():
    """把 service 异常翻成 HTTP 状态码（模式同其它 router）。"""
    try:
        yield
    except ExportScopeError as exc:
        # 范围没选中任何题目 / 参数与 scope 不匹配：请求本身有问题
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExportUnavailableError as exc:
        # 运行环境缺 PDF 依赖（GTK 原生库 / 中文字体）
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _content_disposition(filename: str) -> str:
    """构造 Content-Disposition。

    文件名是中文，必须按 RFC 5987 给出 filename*=UTF-8''…，
    同时给一个 ASCII 回退名：老浏览器不认 filename* 时会用回退名，
    否则可能存成乱码或干脆不带扩展名。
    """
    quoted = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"cuotiben-export.pdf\"; filename*=UTF-8''{quoted}"


@router.post("/export/pdf")
async def export_pdf(payload: ExportPdfRequest, db: Session = Depends(get_db)):
    """导出 PDF，直接返回二进制流（不落盘）。"""
    with _service_errors():
        result = await run_in_threadpool(export_service.export_pdf, db, payload)

        # 记录导出历史（requirements 3.9，可选）。写在同一个线程池任务里，
        # 保证"返回了 PDF"与"记录了历史"一致。
        def _record() -> None:
            try:
                export_service.record_export(db, result)
            except Exception:
                # 历史记录失败不该让已经渲染好的 PDF 白做
                db.rollback()

        await run_in_threadpool(_record)

    return StreamingResponse(
        iter([result.pdf]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(result.filename),
            "Content-Length": str(len(result.pdf)),
            # 便于前端提示"共 N 题"，也方便用 curl 校验
            "X-Question-Count": str(result.question_count),
        },
    )
