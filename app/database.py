"""数据库引擎与会话。

SQLite 位于 data/cuotiben.db（requirements.md 1.4 / 2.17）。

两个必须注意的点：
  1. SQLite 默认不校验外键。必须对每个连接执行 PRAGMA foreign_keys=ON，
     否则 ON DELETE CASCADE 只是摆设。这里用 connect 事件统一打开。
  2. 迁移留口子：目前用 Base.metadata.create_all() 建表；将来换 PostgreSQL
     或引入 Alembic 时，只需替换 engine / 初始化方式，业务层不用动。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# 项目根：app/database.py -> app/ -> 根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "cuotiben.db"


def database_url() -> str:
    """数据库连接串。可用环境变量 CUOTIBEN_DATABASE_URL 覆盖（测试用内存库）。"""
    override = os.environ.get("CUOTIBEN_DATABASE_URL")
    if override:
        return override
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DB_PATH.as_posix()}"


def create_db_engine(url: str | None = None) -> Engine:
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
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            class_=Session)


def init_db(target_engine: Engine | None = None) -> None:
    """建表（幂等）。首次启动时调用。"""
    Base.metadata.create_all(bind=target_engine or engine)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖：请求级会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
