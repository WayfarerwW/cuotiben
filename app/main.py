"""FastAPI 应用入口。

启动流程（lifespan）：
  1. Base.metadata.create_all(bind=engine) —— 建表（幂等）
  2. 写入 settings 默认值 —— 已存在的 key 不覆盖，只补缺失项

注意：建表依赖 app.models 已被导入（init_db 内部会 import），
否则 Base.metadata 里没有表定义，create_all 会静默什么都不建。

按 AGENTS.md 3.4，业务逻辑在 services/；本模块只做调度与挂载：
settings 默认值的写入逻辑已迁到 services/settings_service.py。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.engine import make_url

from .database import SessionLocal, database_url, engine, init_db
from .services.settings_service import ensure_default_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭钩子。"""
    # 1. 建表（init_db 内部会 import app.models 注册全部模型）
    init_db()

    # 2. 初始化 settings 默认值（幂等，不覆盖用户配置）
    with SessionLocal() as db:
        created = ensure_default_settings(db)
    if created:
        print(f"[startup] 已写入默认配置: {', '.join(created)}")
    else:
        print("[startup] settings 默认配置已存在，未改动")

    print(f"[startup] 数据库: {resolve_db_path()}")
    print(f"[startup] 连接串: {database_url()}")
    yield
    engine.dispose()


app = FastAPI(
    title="错题本",
    description="纯本地单机错题管理应用",
    version="0.1.0",
    lifespan=lifespan,
)

# 纯本地运行；放开本地来源，方便前端调试与局域网手机访问
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def resolve_db_path() -> Path | None:
    """从当前 engine 的连接串解析出真实的数据库文件路径。

    不能用 database.DB_PATH：那只是默认路径常量，当 CUOTIBEN_DATABASE_URL
    覆盖时（测试、换盘存放）就不再是实际使用的库。health / 数据说明页
    （requirements.md 2.17）必须显示真实路径。
    """
    database = make_url(str(engine.url)).database
    if not database:
        return None
    return Path(database)


@app.get("/health", tags=["meta"], summary="健康检查")
def health() -> dict:
    """返回服务状态与实际数据库路径，便于确认是否连到预期文件。"""
    db_path = resolve_db_path()
    return {
        "status": "ok",
        "db_path": str(db_path) if db_path else None,
        "db_exists": db_path.exists() if db_path else False,
    }


def register_routers(app: FastAPI) -> None:
    """统一挂载所有业务路由（AGENTS.md 3.4）。

    各模块的 router 在 app/routers/__init__.py 汇总为 api_router，
    这里只负责 include 一次，不在业务模块里各自注册。
    """
    from .routers import api_router

    app.include_router(api_router)


def mount_static(app: FastAPI) -> None:
    """挂载 uploads/ 为静态目录，使图片能通过 /uploads/... 直接访问。

    路径前缀必须与 image_service.UPLOADS_URL_PREFIX 一致，
    否则 url_for() 生成的地址前端打不开。
    """
    from fastapi.staticfiles import StaticFiles

    from .services.image_service import UPLOADS_DIR, UPLOADS_URL_PREFIX

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        UPLOADS_URL_PREFIX,
        StaticFiles(directory=str(UPLOADS_DIR)),
        name="uploads",
    )


# 模块加载时完成挂载（放在函数定义之后）
register_routers(app)
mount_static(app)