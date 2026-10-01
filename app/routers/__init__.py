"""路由层。

按 AGENTS.md 3.4：各业务模块在 routers/xxx.py 定义 router，
在这里汇总成 api_router，由 main.py 的 register_routers(app) 一次性挂载。
新增模块时在下面加两行（import + include_router）。
"""

from __future__ import annotations

from fastapi import APIRouter

from . import folders, notes, questions, review, settings, tags, upload

api_router = APIRouter()
api_router.include_router(folders.router)
api_router.include_router(notes.router)
api_router.include_router(questions.router)
api_router.include_router(review.router)
api_router.include_router(settings.router)
api_router.include_router(tags.router)
api_router.include_router(upload.router)

__all__ = ["api_router"]
