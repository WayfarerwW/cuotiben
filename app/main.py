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

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

from .database import SessionLocal, database_url, engine, init_db
from .services.settings_service import ensure_default_settings
from .services.sync_service import start_background_sync, stop_background_sync

# 前端静态资源目录（index.html / css / js）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭钩子。"""
    # 0. 读 .env（AGENTS.md 五的同步配置在这里）。
    #    放在最前面：数据库连接串等也可能来自 .env。
    #    load_dotenv 默认 override=False，即**真实环境变量优先**，
    #    方便临时覆盖而不改文件。
    load_dotenv(PROJECT_ROOT / ".env")

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

    # 3. GitHub 自动同步（AGENTS.md 五）。
    #    默认关闭；只有 GIT_SYNC_ENABLED=1 且配了 repo_url 时才起线程。
    #    必须放在 lifespan 里而不是模块加载时：run.py 用了 uvicorn 的
    #    reload=True，模块会被加载两次（reloader 父进程 + 工作子进程），
    #    放模块级会起两份线程、同步两次。lifespan 只在工作进程里跑。
    sync_boot = start_background_sync(run_first=True)
    if not sync_boot.get("started"):
        print(f"[sync] 未启动：{sync_boot.get('reason')}")

    yield

    stop_background_sync()
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
    """挂载静态资源。

    **顺序很关键**：本函数必须在所有 API 路由注册**之后**调用。

    两个挂载点：
      - /uploads  题目图片（前缀须与 image_service.UPLOADS_URL_PREFIX 一致，
                  否则 url_for() 生成的地址前端打不开）
      - /static   前端资源的规范前缀（可直接 /static/css/style.css 访问）

    另外**必须一并启用 serve_frontend_assets 中间件**，原因：
    index.html 里引用的是相对路径（`css/style.css`、`js/app.js`），
    浏览器会解析成 `/css/style.css` 而不是 `/static/css/style.css`。
    只挂 /static 的话这些引用全部 404，页面白屏。

    这里没有选择"把静态目录挂到 `/`"，因为那样会让所有未匹配路径都落到
    静态查找上，API 的 404 语义会变得混乱。改用只认 css/js/vendor 等
    已知顶层目录的中间件（见下），范围明确且不影响 API。
    """
    from fastapi.staticfiles import StaticFiles

    from .services.image_service import UPLOADS_DIR, UPLOADS_URL_PREFIX

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        UPLOADS_URL_PREFIX,
        StaticFiles(directory=str(UPLOADS_DIR)),
        name="uploads",
    )

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )


# 允许从根路径直接访问的前端资源顶层目录。
# 只放这些，避免把整个静态目录暴露到 `/` 下。
FRONTEND_ASSET_DIRS = ("css", "js", "assets", "img", "fonts")


def serve_frontend_assets(app: FastAPI) -> None:
    """让 index.html 里的**相对路径**引用能在根路径下命中。

    `css/style.css` 会被浏览器解析成 `/css/style.css`，因此需要有人
    把这类请求映射到 app/static/css/style.css。

    为什么用中间件而不是再加几个 Route：路由是在应用启动时定死的，
    而静态目录里的文件是运行时可增删的；中间件在每次请求时判断
    文件是否存在，行为与 StaticFiles 一致。

    为什么不用 mount("/")：那会让所有未匹配的路径都进静态查找，
    API 的 404 会变成"静态文件找不到"，语义混乱。这里只处理
    已知顶层目录（css/js/...），范围明确。
    """
    from fastapi import Request
    from fastapi.responses import FileResponse

    # 已被专门挂载或属于 API 的前缀，不再由本中间件处理
    skip_prefixes = ("/uploads", "/static", "/docs", "/redoc", "/openapi.json")

    @app.middleware("http")
    async def _frontend_assets(request: Request, call_next):
        path = request.url.path
        if request.method in ("GET", "HEAD") and not path.startswith(skip_prefixes):
            rel = path.lstrip("/")
            top = rel.split("/", 1)[0]
            if top in FRONTEND_ASSET_DIRS:
                candidate = (STATIC_DIR / rel).resolve()
                # 防目录穿越：解析后必须仍在静态目录内
                try:
                    candidate.relative_to(STATIC_DIR.resolve())
                except ValueError:
                    candidate = None
                if candidate and candidate.is_file():
                    return FileResponse(candidate)
        return await call_next(request)


def register_index(app: FastAPI) -> None:
    """注册根路径，返回前端入口页（requirements.md 8：访问 localhost 即可用）。

    必须放在最后：`/` 本身只精确匹配根路径，但它代表"前端入口"，
    放在 API 与静态挂载之后能保证任何 API 前缀都优先被处理。

    这里用 FileResponse 直接返回文件，而不是把静态目录挂到 `/`：
    后者会让未匹配的路径全部落到静态查找上，API 的 404 语义会变得混乱。
    """
    from fastapi.responses import FileResponse

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


def register_test_harness(app: FastAPI) -> None:
    """仅测试用：把一个外部 HTML 文件挂到 `/__harness`。

    为什么需要它：验证"页面真的能打开"时，测试脚本必须与被测页面**同源**
    才能读取页面 DOM（跨源会被浏览器拦截）。提供一个把测试页放在同源的
    途径，比在测试里另外起一个 origin 再被拦截要可靠。

    默认关闭；只有显式设置 `CUOTIBEN_TEST_HARNESS=<html 文件路径>` 时才启用，
    因此对正常运行没有任何影响。
    """
    from fastapi.responses import FileResponse

    harness = os.environ.get("CUOTIBEN_TEST_HARNESS")
    if not harness or not Path(harness).is_file():
        return

    harness_path = Path(harness)

    @app.get("/__harness", include_in_schema=False)
    def _harness() -> FileResponse:
        return FileResponse(harness_path, media_type="text/html")


# 模块加载时完成注册与挂载。
# 顺序：API 路由 → 静态挂载 → 前端相对资源中间件 → 根路径 → 测试辅助路由。
register_routers(app)
mount_static(app)
serve_frontend_assets(app)
register_index(app)
register_test_harness(app)