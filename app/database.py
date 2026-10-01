"""数据库引擎、会话与依赖。

SQLite 文件位于 data/cuotiben.db（requirements.md 1.4 / 2.17）。

三个关键点：
  1. SQLite 默认不校验外键。必须对每个连接执行 PRAGMA foreign_keys=ON，
     否则 ON DELETE CASCADE 形同虚设。这里用 connect 事件统一打开。
  2. `Base` 在 app.database 定义（本模块不 import app.models，避免循环导入），
     app.models.base 再把它转出去，所以 `from app.models import Base` 依然可用。
  3. 迁移留口子：目前用 Base.metadata.create_all() 建表；将来换 PostgreSQL 或
     引入 Alembic，只需替换 engine / init_db，业务层不用动。需要建表前必须先
     import app.models（见 init_db）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# 项目根：app/database.py -> app/ -> 根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "cuotiben.db"


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。

    定义在这里而不是 app/models/base.py，是为了让 engine / SessionLocal / Base
    三者集中在同一模块；app/models/base.py 会把它再导出。
    """


def database_url() -> str:
    """数据库连接串。可用环境变量 CUOTIBEN_DATABASE_URL 覆盖（测试用内存库）。"""
    override = os.environ.get("CUOTIBEN_DATABASE_URL")
    if override:
        return override
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DB_PATH.as_posix()}"


def create_db_engine(url: str | None = None) -> Engine:
    """创建引擎并挂上 SQLite 外键开关。

    传 url 可拿到一个独立引擎（自检脚本/测试用），不影响全局 engine。
    """
    url = url or database_url()
    # check_same_thread=False：FastAPI 的线程池会跨线程复用连接
    engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        """SQLite 每个连接都要单独开启外键约束。"""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = create_db_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session
)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级数据库会话。

    路由里用 `db: Session = Depends(get_db)` 注入；请求结束自动关闭。
    不在这里自动 commit —— 由 services 决定事务边界。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 兼容旧名字（自检脚本早期用的是 get_session）
get_session = get_db


def init_db(target_engine: Engine | None = None) -> None:
    """建表（幂等）。

    必须先 import app.models 把所有模型注册进 Base.metadata，
    否则 create_all 会漏建表。
    """
    import app.models  # noqa: F401  （注册全部模型）

    Base.metadata.create_all(bind=target_engine or engine)
