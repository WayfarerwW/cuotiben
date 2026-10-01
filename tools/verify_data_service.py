"""数据说明页的 service 级验证（requirements.md 2.17）。

覆盖：
- GET /data/paths 的字段与"路径由服务端解析"（不是硬编码字面值）
- POST /data/backup：目录结构、库文件可打开且内容完整、uploads 一并复制、
  同一秒重复备份不互相覆盖、库不存在时 404 语义
- GET /data/export：JSON 结构、各实体条数、软删除记录也导出、
  图片只存路径不内联、中文不被转义成 \\uXXXX、文件名含日期

不用 pytest，照本项目惯例直接跑、打印 [PASS]/[FAIL]、用退出码表示结果。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK = FAIL = 0
FAILED: list[str] = []


def expect(label: str, cond: bool, detail: object = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        FAILED.append(label)
    print(("[PASS] " if cond else "[FAIL] ") + label
          + (f"  ({detail})" if detail != "" else ""))


def section(title: str) -> None:
    print(f"\n-- {title} --")


def main() -> int:  # noqa: C901 - 线性用例清单
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import database
    from app.database import Base
    from app.schemas import BackupResultOut, DataPathsOut
    from app.services import data_service, question_service, review_service

    tmp = Path(tempfile.mkdtemp(prefix="verify_data_"))
    db_file = tmp / "d.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    # 把 data_service 的工作目录指到临时目录，避免污染真实 backups/ 与 uploads/
    data_service.BACKUPS_DIR = tmp / "backups"
    data_service.UPLOADS_DIR = tmp / "uploads"
    data_service.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    # 让 resolve_db_path() 指向临时库
    original_engine = database.engine
    database.engine = engine

    try:
        return run_cases(db, tmp, engine, data_service, question_service,
                         review_service, DataPathsOut, BackupResultOut)
    finally:
        database.engine = original_engine


def run_cases(db, tmp, engine, data_service, question_service,  # noqa: C901
              review_service, DataPathsOut, BackupResultOut) -> int:
    from app.models import Folder, Note, Question, Tag
    from app.schemas import ExportPdfRequest  # noqa: F401  (确保 schemas 可导入)

    # ---------------- 造数据 ----------------
    subject = Folder(name="高等数学", parent_id=None, sort_order=0)
    db.add(subject)
    db.commit()
    leaf = Folder(name="极限与连续", parent_id=subject.id, sort_order=0)
    db.add(leaf)
    db.commit()

    tag_a, tag_b = Tag(name="重要极限"), Tag(name="可导条件")
    db.add_all([tag_a, tag_b])
    db.commit()

    q1 = Question(folder_id=leaf.id, stem="求极限 sin(x)/x", answer="答案 1",
                  is_starred=True, mastery_status="still_wrong", sort_order=0)
    q1.tags = [tag_a]
    q2 = Question(folder_id=leaf.id, stem="求导数 y=x^3", answer="答案 2",
                  is_starred=False, mastery_status="mastered", sort_order=1)
    q2.tags = [tag_a, tag_b]
    db.add_all([q1, q2])
    db.commit()

    # 一张软删除的题：导出必须带上，否则还原时上下文会丢
    q3 = Question(folder_id=leaf.id, stem="已删除的题", answer="x",
                  is_starred=False, mastery_status="still_wrong", sort_order=2)
    db.add(q3)
    db.commit()
    question_service.delete_question(db, q3.id)

    review_service.check(db, q1.id, mastery=3)
    note = Note(title="笔记标题", content="笔记内容", question_id=q1.id)
    db.add(note)
    db.commit()

    # 图片占位文件 + 一条 question_images 记录
    img_dir = data_service.UPLOADS_DIR / "2026" / "02" / "14"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    from app.models import QuestionImage
    db.add(QuestionImage(question_id=q1.id, file_path="/uploads/2026/02/14/a.jpg",
                         width=10, height=10, size=12, sort_order=0))
    db.commit()

    # ---------------- GET /data/paths ----------------
    section("GET /data/paths")
    paths = data_service.data_paths()
    out = DataPathsOut(**vars(paths))
    expect("返回 DataPathsOut 契约", isinstance(out, DataPathsOut))
    expect("db_path 指向实际使用的库文件",
           Path(out.db_path) == Path(str(engine.url.database))
           and Path(out.db_path).is_file(),
           out.db_path)
    expect("db_exists 为真", out.db_exists is True)
    expect("uploads_path 已给出", out.uploads_path.endswith("uploads"), out.uploads_path)
    expect("uploads_file_count 统计到图片文件", out.uploads_file_count == 1,
           out.uploads_file_count)
    expect("backups_path 已给出", out.backups_path.endswith("backups"), out.backups_path)
    expect("backups 目录不存在时 backups_exists=False",
           out.backups_exists is False, out.backups_exists)
    expect("路径**不是**硬编码的 data/cuotiben.db（由服务端解析）",
           "cuotiben.db" not in Path(out.db_path).name, Path(out.db_path).name)

    # ---------------- POST /data/backup ----------------
    section("POST /data/backup")
    moment = dt.datetime(2026, 2, 14, 10, 30, tzinfo=dt.timezone.utc)
    res = data_service.create_backup(now=moment)
    backup = Path(res.backup_dir)
    expect("返回 BackupResultOut 契约", isinstance(BackupResultOut(**vars(res)),
                                                BackupResultOut))
    expect("备份目录已创建", backup.is_dir(), backup.name)
    expect("备份目录位于 backups/ 下", backup.parent == data_service.BACKUPS_DIR)
    # 目录名用**本地时区**（2026-02-14 10:30 UTC = 18:30 +08:00）：
    # 备份目录是给人看的，用本地时间才符合直觉。第一版按 UTC 断言，写错了。
    expect("目录名含日期与本地时间",
           "20260214" in backup.name and "183000" in backup.name, backup.name)
    expect("数据库文件已复制", (backup / "d.db").is_file())
    expect("db_bytes 与实际文件一致",
           res.db_bytes == (backup / "d.db").stat().st_size, res.db_bytes)
    expect("uploads/ 一并复制", (backup / "uploads").is_dir())
    expect("图片数量正确", res.image_count == 1, res.image_count)
    expect("图片内容被完整复制",
           (backup / "uploads/2026/02/14/a.jpg").read_bytes()
           == b"\xff\xd8\xff\xe0fake-jpeg")

    # 备份出的库能打开、且数据完整（用 sqlite3 直连，验证是有效数据库）
    con = sqlite3.connect(str(backup / "d.db"))
    try:
        n_q = con.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        n_f = con.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
        n_r = con.execute("SELECT COUNT(*) FROM review_records").fetchone()[0]
    finally:
        con.close()
    expect("备份库可打开且题目数正确", n_q == 3, f"{n_q} 行（含软删除）")
    expect("备份库文件夹数正确", n_f == 2, n_f)
    expect("备份库复习记录数正确", n_r >= 1, n_r)

    # 同一秒重复备份不覆盖
    res2 = data_service.create_backup(now=moment)
    backup2 = Path(res2.backup_dir)
    expect("同一秒重复备份不覆盖已有备份",
           backup2 != backup and backup2.is_dir() and backup.is_dir(),
           f"{backup.name} / {backup2.name}")

    # ---------------- GET /data/export ----------------
    section("GET /data/export")
    payload = data_service.export_json(db, now=moment)
    expect("有 schema_version", payload.get("schema_version") == 1)
    expect("有 exported_at", "exported_at" in payload)
    expect("counts 与实际一致",
           payload["counts"]["folders"] == 2
           and payload["counts"]["questions"] == 3
           and payload["counts"]["tags"] == 2
           and payload["counts"]["notes"] == 1,
           payload["counts"])
    expect("导出全部题（含软删除）", len(payload["questions"]) == 3)
    deleted = [q for q in payload["questions"] if q["deleted_at"]]
    expect("软删除的题带 deleted_at", len(deleted) == 1, len(deleted))
    expect("题目带 tags 名称列表",
           sorted(payload["questions"][0]["tags"]) == ["重要极限"],
           payload["questions"][0]["tags"])
    expect("题目带 images 路径列表",
           payload["questions"][0]["images"] == ["/uploads/2026/02/14/a.jpg"],
           payload["questions"][0]["images"])
    expect("有 settings 段", isinstance(payload.get("settings"), list))
    expect("有 review_records 段", len(payload["review_records"]) >= 1)
    expect("有 export_records 段（空也保留结构）",
           isinstance(payload.get("export_records"), list))
    blob = json.dumps(payload)
    expect("图片未内联 base64", "base64" not in blob and "data:image" not in blob)

    raw = data_service.export_json_bytes(db, now=moment)
    expect("导出字节是合法 UTF-8 JSON",
           isinstance(json.loads(raw.decode("utf-8")), dict))
    expect("中文不被转义成 \\uXXXX（ensure_ascii=False）",
           "求极限".encode("utf-8") in raw, "含原始中文")
    expect("导出的 JSON 是缩进过的（可读）", b"\n  " in raw)

    fname = data_service.json_filename(now=moment)
    expect("JSON 文件名含日期", fname.startswith("20260214"), fname)
    expect("JSON 文件名以 .json 结尾", fname.endswith(".json"), fname)

    # ---------------- 库不存在 ----------------
    section("边界：数据库不存在")
    missing = tmp / "nope.db"
    data_service.resolve_db_path = lambda: missing  # 临时替换
    try:
        data_service.create_backup(now=moment)
        expect("库不存在时抛 NothingToBackupError", False, "没有抛异常")
    except data_service.NothingToBackupError as exc:
        expect("库不存在时抛 NothingToBackupError", True, str(exc)[:36])
    except Exception as exc:  # noqa: BLE001
        expect("库不存在时抛 NothingToBackupError", False,
               f"抛了 {type(exc).__name__}")
    finally:
        del data_service.resolve_db_path  # 恢复模块原函数

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
