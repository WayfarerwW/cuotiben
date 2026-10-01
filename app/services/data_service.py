"""数据说明页的服务端能力：手动备份 + 导出 JSON（requirements.md 2.17）。

为什么要有后端，而不是让用户自己去复制文件：
2.17 要求提供"手动备份、导出 JSON **入口**"。既然页面上有按钮，按钮就必须
真的做事 —— 只显示路径让用户自己去资源管理器里拷，那不叫入口。

两个能力都作用于**本机文件系统**，不联网、不产生任何外发流量。

分层（AGENTS.md 3.4）：本模块接收 Session、抛业务异常，router 只做组装。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from ..database import PROJECT_ROOT
from ..models.export_records import ExportRecord
from ..models.folders import Folder
from ..models.notes import Note
from ..models.question_images import QuestionImage
from ..models.questions import Question
from ..models.review_records import ReviewRecord
from ..models.settings import Setting
from ..models.tags import Tag

def _env_dir(env_name: str, default: Path) -> Path:
    """目录来源：环境变量优先，否则项目内默认位置。

    为什么要可覆盖：备份会把整个 uploads/ 复制一遍。
    测试必须在临时目录里做这件事 —— 否则每跑一次自检就往真实的
    backups/ 里堆一份，跑几十次后既脏又慢（实测因此让浏览器验证超时）。
    """
    raw = os.environ.get(env_name)
    return Path(raw) if raw else default


BACKUPS_DIR = _env_dir("CUOTIBEN_BACKUPS_DIR", PROJECT_ROOT / "backups")
UPLOADS_DIR = _env_dir("CUOTIBEN_UPLOADS_DIR", PROJECT_ROOT / "uploads")

#: 导出 JSON 的格式版本。将来若改结构，靠它区分。
JSON_SCHEMA_VERSION = 1


class BackupError(Exception):
    """备份/导出相关错误的基类。"""


class NothingToBackupError(BackupError):
    """数据库文件不存在，没什么可备份的。"""


# --------------------------------------------------------------------------
# 路径信息
# --------------------------------------------------------------------------


@dataclass
class DataPaths:
    """数据说明页要展示的路径与存在性。"""

    db_path: str
    db_exists: bool
    uploads_path: str
    uploads_exists: bool
    uploads_file_count: int
    backups_path: str
    backups_exists: bool


def resolve_db_path() -> Path:
    """当前实际使用的 SQLite 文件路径。

    不能用常量 DB_PATH：CUOTIBEN_DATABASE_URL 可覆盖（测试就是这么跑的），
    页面必须显示**真正在用的**那个文件，否则会误导用户去备份一个空文件。
    """
    from ..database import engine

    url = engine.url
    if url.get_backend_name() != "sqlite" or not url.database:
        raise BackupError(f"当前后端不是 SQLite（{url.get_backend_name()}），暂不支持备份")
    return Path(url.database)


def data_paths() -> DataPaths:
    """采集页面需要的路径信息（只读，不修改任何文件）。"""
    db = resolve_db_path()
    uploads_count = 0
    if UPLOADS_DIR.is_dir():
        uploads_count = sum(1 for p in UPLOADS_DIR.rglob("*") if p.is_file())
    return DataPaths(
        db_path=str(db),
        db_exists=db.is_file(),
        uploads_path=str(UPLOADS_DIR),
        uploads_exists=UPLOADS_DIR.is_dir(),
        uploads_file_count=uploads_count,
        backups_path=str(BACKUPS_DIR),
        backups_exists=BACKUPS_DIR.is_dir(),
    )


# --------------------------------------------------------------------------
# 手动备份
# --------------------------------------------------------------------------


@dataclass
class BackupResult:
    """一次备份的产物。"""

    backup_dir: str
    db_bytes: int
    image_count: int
    created_at: str


def create_backup(*, now: dt.datetime | None = None) -> BackupResult:
    """把数据库文件与 uploads/ 复制到 backups/<时间戳>/。

    用**复制**而不是移动/重命名：备份不该影响正在运行的数据库。
    时间戳精确到秒并带序号兜底，避免同一秒内连点两次导致目录互相覆盖。
    """
    moment = now or dt.datetime.now(dt.timezone.utc)
    db = resolve_db_path()
    if not db.is_file():
        raise NothingToBackupError(f"数据库文件不存在：{db}")

    stamp = moment.astimezone().strftime("%Y%m%d-%H%M%S")
    target = BACKUPS_DIR / stamp
    if target.exists():
        # 同一秒重复备份：加后缀，不覆盖已有备份
        for i in range(2, 100):
            candidate = BACKUPS_DIR / f"{stamp}-{i}"
            if not candidate.exists():
                target = candidate
                break
        else:  # pragma: no cover - 一百次同秒备份，实际不会发生
            raise BackupError("同一秒内备份次数过多，请稍后再试")
    target.mkdir(parents=True, exist_ok=False)

    # 数据库：用 SQLite 自己的 backup API，而不是 shutil.copy2。
    # 直接拷 .db 文件在有未提交事务/WAL 时可能拿到不一致的快照。
    dest_db = target / db.name
    _sqlite_backup(db, dest_db)

    image_count = 0
    if UPLOADS_DIR.is_dir():
        dest_uploads = target / "uploads"
        shutil.copytree(UPLOADS_DIR, dest_uploads)
        image_count = sum(1 for p in dest_uploads.rglob("*") if p.is_file())

    return BackupResult(
        backup_dir=str(target),
        db_bytes=dest_db.stat().st_size,
        image_count=image_count,
        created_at=moment.isoformat(),
    )


def _sqlite_backup(source: Path, dest: Path) -> None:
    """用 sqlite3 的 backup() 做一致性快照。"""
    import sqlite3

    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


# --------------------------------------------------------------------------
# 导出 JSON
# --------------------------------------------------------------------------


def _iso(value: object) -> str | None:
    """datetime -> ISO 字符串；None 保持 None。"""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return str(value)


def export_json(db: Session, *, now: dt.datetime | None = None) -> dict:
    """把全部业务数据导出成一个 dict（供序列化成 JSON 文件/响应体）。

    设计取舍：
      - **不用 ORM 的自动序列化**（如 model_dump）：那会把实现细节绑到表结构上，
        字段改名就会悄悄改变导出格式。这里显式逐字段映射，导出格式是**契约**。
      - 图片只导出**引用路径**，不内联二进制：JSON 是给人看/做迁移用的，
        内联 base64 会让文件大到没法用。图片本身在 uploads/，一起备份即可。
      - 软删除的记录也导出（带 deleted_at），否则还原时上下文就丢了。
    """
    moment = now or dt.datetime.now(dt.timezone.utc)

    folders = [
        {
            "id": f.id, "name": f.name, "parent_id": f.parent_id,
            "level": f.level, "sort_order": f.sort_order,
            "created_at": _iso(f.created_at), "deleted_at": _iso(f.deleted_at),
        }
        for f in db.query(Folder).order_by(Folder.id).all()
    ]

    tags = [
        {"id": t.id, "name": t.name, "created_at": _iso(t.created_at)}
        for t in db.query(Tag).order_by(Tag.id).all()
    ]

    questions = [
        {
            "id": q.id, "folder_id": q.folder_id, "stem": q.stem, "answer": q.answer,
            "is_starred": q.is_starred, "mastery_status": q.mastery_status,
            "sort_order": q.sort_order,
            "tags": sorted(t.name for t in q.tags),
            "images": [
                i.file_path for i in sorted(q.images, key=lambda x: (x.sort_order, x.id))
            ],
            "created_at": _iso(q.created_at), "updated_at": _iso(q.updated_at),
            "deleted_at": _iso(q.deleted_at),
        }
        for q in db.query(Question).order_by(Question.id).all()
    ]

    images = [
        {
            "id": i.id, "question_id": i.question_id, "file_path": i.file_path,
            "original_path": i.original_path, "width": i.width, "height": i.height,
            "size": i.size, "sort_order": i.sort_order,
            "created_at": _iso(i.created_at),
        }
        for i in db.query(QuestionImage).order_by(QuestionImage.id).all()
    ]

    reviews = [
        {
            "id": r.id, "question_id": r.question_id,
            "review_count": r.review_count, "interval_index": r.interval_index,
            "last_review_at": _iso(r.last_review_at),
            "next_review_at": _iso(r.next_review_at),
            "mastery_level": r.mastery_level, "is_backfill": r.is_backfill,
            "created_at": _iso(r.created_at), "deleted_at": _iso(r.deleted_at),
        }
        for r in db.query(ReviewRecord).order_by(ReviewRecord.id).all()
    ]

    notes = [
        {
            "id": n.id, "title": n.title, "content": n.content,
            "question_id": n.question_id,
            "created_at": _iso(n.created_at), "updated_at": _iso(n.updated_at),
            "deleted_at": _iso(n.deleted_at),
        }
        for n in db.query(Note).order_by(Note.id).all()
    ]

    settings = [
        {"key": s.key, "value": s.value} for s in db.query(Setting).order_by(Setting.key).all()
    ]

    exports = [
        {
            "id": e.id, "range_desc": e.range_desc,
            "question_count": e.question_count, "file_path": e.file_path,
            "created_at": _iso(e.created_at),
        }
        for e in db.query(ExportRecord).order_by(ExportRecord.id).all()
    ]

    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "exported_at": moment.isoformat(),
        "app": "错题本",
        "counts": {
            "folders": len(folders), "tags": len(tags), "questions": len(questions),
            "question_images": len(images), "review_records": len(reviews),
            "notes": len(notes), "settings": len(settings),
        },
        "folders": folders,
        "tags": tags,
        "questions": questions,
        "question_images": images,
        "review_records": reviews,
        "notes": notes,
        "settings": settings,
        "export_records": exports,
    }


def export_json_bytes(db: Session, *, now: dt.datetime | None = None) -> bytes:
    """导出为 UTF-8 JSON 字节串（ensure_ascii=False，保持中文可读）。"""
    payload = export_json(db, now=now)
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def json_filename(*, now: dt.datetime | None = None) -> str:
    """导出文件名含日期，与 PDF 导出一致的习惯。"""
    moment = now or dt.datetime.now(dt.timezone.utc)
    return f"{moment.astimezone():%Y%m%d}_错题本_数据导出.json"
