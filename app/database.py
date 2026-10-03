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


def init_db(target_engine: Engine | None = None) -> list[str]:
    """建表（幂等），然后跑一遍轻量迁移。

    必须先 import app.models 把所有模型注册进 Base.metadata，
    否则 create_all 会漏建表。

    返回本次实际执行的迁移（空列表 = 库是最新的），供启动日志打印。
    """
    import app.models  # noqa: F401  （注册全部模型）

    engine_ = target_engine or engine
    Base.metadata.create_all(bind=engine_)
    return run_light_migrations(engine_)


#: 轻量迁移：给**已存在**的表补列。
#:
#: 为什么需要它：`create_all` 只建缺失的**表**，绝不会给已存在的表加列。
#: 用户的 data/cuotiben.db 是长期保留的，所以加字段后必须显式 ALTER，
#: 否则升级后所有查询都会报 "no such column"。
#:
#: 项目约定是"迁移留口子"（AGENTS.md 七）：这里只做**幂等、可自动完成**的
#: 加列，遇到需要搬数据/改约束的复杂迁移再引入 Alembic。
#: 每条 `(表, 列, 类型, 默认值)`：列不存在才 ALTER，因此可反复执行。
LIGHT_MIGRATIONS: tuple[tuple[str, str, str, str], ...] = (
    # question_images.kind：区分题干图 / 答案图（requirements 3.3）。
    # 默认 stem —— 加字段之前只存在一种位置，老数据的语义就是题干图。
    ("question_images", "kind", "VARCHAR(16)", "stem"),
    # review_records.mastery_streak：连续达到「已掌握」的次数（requirements 2.14）。
    # 默认 0 —— 加字段之前的打勾都发生在"还没有连续计数"的旧规则下，
    # 从 0 起算最保守：用户需要重新连续攒够次数才会毕业。
    ("review_records", "mastery_streak", "INTEGER", "0"),
)


def run_light_migrations(target_engine: Engine | None = None) -> list[str]:
    """执行 LIGHT_MIGRATIONS，返回本次实际做的改动（供启动日志）。"""
    from sqlalchemy import inspect, text

    engine_ = target_engine or engine
    applied: list[str] = []
    inspector = inspect(engine_)
    existing_tables = set(inspector.get_table_names())

    for table, column, coltype, default in LIGHT_MIGRATIONS:
        if table not in existing_tables:
            continue                      # 新库由 create_all 直接建好，不用迁移
        columns = {c["name"] for c in inspector.get_columns(table)}
        if column in columns:
            continue                      # 幂等：已经有了就跳过
        with engine_.begin() as conn:
            quoted_default = f"'{default}'"
            conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN {column} {coltype} "
                f"NOT NULL DEFAULT {quoted_default}"
            ))
            # 显式回填一次：SQLite 的 ADD COLUMN DEFAULT 会给已有行填上，
            # 但不能假设所有后端都如此（将来换 PostgreSQL 时这条更保险）。
            conn.execute(text(
                f"UPDATE {table} SET {column} = :d WHERE {column} IS NULL"
            ), {"d": default})
        applied.append(f"{table}.{column}={default}")
        # 表结构变了，重建 inspector 以便后续条目看到最新列
        inspector = inspect(engine_)

    return applied
