"""ORM 模型自检：建表、校验字段、验证 ON DELETE CASCADE 与软删除语义。

覆盖 AGENTS.md 3.1 与 requirements.md 3.10 的硬约束：
  - 九张表全部建成
  - 所有 datetime 列都是 DateTime(timezone=True)
  - 所有外键都带 ondelete='CASCADE'
  - 软删除字段存在
  - SQLite 外键约束真的生效（级联删除可用）

运行：python tools/verify_models.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import DateTime, inspect  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import create_db_engine  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    ExportRecord,
    Folder,
    Note,
    Question,
    QuestionImage,
    ReviewRecord,
    Setting,
    Tag,
)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# requirements.md 第 3 节定义的表
EXPECTED_TABLES = {
    "folders", "questions", "question_images", "tags",
    "question_tags", "review_records", "notes", "settings", "export_records",
}

# 各表的外键列（必须 ondelete=CASCADE，notes.question_id 为 SET NULL）
EXPECTED_FK_CASCADE = {
    "folders": ["parent_id"],
    "questions": ["folder_id"],
    "question_images": ["question_id"],
    "question_tags": ["question_id", "tag_id"],
    "review_records": ["question_id"],
}
EXPECTED_FK_SET_NULL = {"notes": ["question_id"]}

# 软删除表
SOFT_DELETE_TABLES = ["folders", "questions", "review_records", "notes"]

# requirements.md 第 3 节逐个列出的字段
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "folders": {"id", "name", "parent_id", "level", "sort_order", "deleted_at", "created_at"},
    "questions": {"id", "folder_id", "stem", "answer", "is_starred", "mastery_status",
                  "sort_order", "created_at", "updated_at", "deleted_at"},
    "question_images": {"id", "question_id", "file_path", "original_path", "width",
                        "height", "size", "sort_order", "created_at"},
    "tags": {"id", "name", "created_at"},
    "question_tags": {"question_id", "tag_id"},
    "review_records": {"id", "question_id", "review_count", "interval_index",
                       "last_review_at", "next_review_at", "mastery_level",
                       "created_at", "deleted_at"},
    "notes": {"id", "title", "content", "question_id", "created_at", "updated_at",
              "deleted_at"},
    "settings": {"id", "key", "value"},
    "export_records": {"id", "range_desc", "question_count", "file_path", "created_at"},
}


def main() -> int:
    print("=" * 78)
    print("ORM 模型自检")
    print("=" * 78)

    # 用临时文件库，避免污染 data/cuotiben.db
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "verify.db"
        engine = create_db_engine(f"sqlite:///{db.as_posix()}")
        Base.metadata.create_all(bind=engine)

        insp = inspect(engine)

        # 1. 表齐全
        tables = set(insp.get_table_names())
        missing = EXPECTED_TABLES - tables
        extra = tables - EXPECTED_TABLES
        check("九张表全部建成", not missing and not extra,
              f"缺失={sorted(missing) or '无'} 多余={sorted(extra) or '无'}")

        # 2. 字段齐全
        col_problems = []
        for tname, expected in EXPECTED_COLUMNS.items():
            actual = {c["name"] for c in insp.get_columns(tname)}
            lack = expected - actual
            if lack:
                col_problems.append(f"{tname} 缺 {sorted(lack)}")
        check("字段与需求第 3 节一致", not col_problems,
              "; ".join(col_problems) if col_problems else "全部匹配")

        # 3. datetime 列都带 timezone=True
        #    注意：SQLite 的 DDL 里 DATETIME 无法表达时区，反射回来必然丢失该标志，
        #    所以必须检查 ORM 元数据（权威声明），不能检查 inspector 反射结果。
        #    本项目用 UTCDateTime(TypeDecorator) 收口，它对外声明 timezone=True。
        from sqlalchemy.types import TypeDecorator

        from app.models.base import UTCDateTime

        def _is_tz_aware_datetime(t) -> bool | None:
            """判断列是否为本项目的 UTC datetime 类型；非 datetime 列返回 None。"""
            if isinstance(t, UTCDateTime):
                return True
            if isinstance(t, DateTime):
                return bool(t.timezone)
            if isinstance(t, TypeDecorator) and isinstance(t.impl, DateTime):
                return bool(t.impl.timezone)
            return None

        tz_problems = []
        tz_count = 0
        for tname, tbl in sorted(Base.metadata.tables.items()):
            for col in tbl.columns:
                verdict = _is_tz_aware_datetime(col.type)
                if verdict is None:
                    continue
                tz_count += 1
                if not verdict:
                    tz_problems.append(f"{tname}.{col.name}")
        check(f"所有 DateTime 均为 timezone=True（ORM 声明，{tz_count} 列）",
              not tz_problems,
              f"未带时区: {tz_problems}" if tz_problems else "全部带时区")

        # 4. 外键带 ON DELETE CASCADE
        fk_problems = []
        for tname, cols in EXPECTED_FK_CASCADE.items():
            fks = {fk["constrained_columns"][0]: fk for fk in insp.get_foreign_keys(tname)}
            for col in cols:
                fk = fks.get(col)
                if fk is None:
                    fk_problems.append(f"{tname}.{col} 无外键")
                elif (fk.get("options") or {}).get("ondelete", "").upper() != "CASCADE":
                    fk_problems.append(f"{tname}.{col} ondelete={fk.get('options')}")
        check("外键均 ON DELETE CASCADE", not fk_problems,
              "; ".join(fk_problems) if fk_problems else "全部 CASCADE")

        # 4b. notes.question_id 用 SET NULL（笔记本身不该被级联删除）
        fk_problems2 = []
        for tname, cols in EXPECTED_FK_SET_NULL.items():
            fks = {fk["constrained_columns"][0]: fk for fk in insp.get_foreign_keys(tname)}
            for col in cols:
                fk = fks.get(col)
                if fk is None:
                    fk_problems2.append(f"{tname}.{col} 无外键")
                elif (fk.get("options") or {}).get("ondelete", "").upper() != "SET NULL":
                    fk_problems2.append(f"{tname}.{col} ondelete={fk.get('options')}")
        check("notes.question_id 为 ON DELETE SET NULL", not fk_problems2,
              "; ".join(fk_problems2) if fk_problems2 else "正确")

        # 5. 软删除字段存在
        sd_problems = [t for t in SOFT_DELETE_TABLES
                       if "deleted_at" not in {c["name"] for c in insp.get_columns(t)}]
        check("软删除字段 deleted_at 存在", not sd_problems,
              f"缺: {sd_problems}" if sd_problems else f"{SOFT_DELETE_TABLES}")

        # 6. tags.name 唯一
        uniques = [u["column_names"] for u in insp.get_unique_constraints("tags")]
        idxs = [tuple(i["column_names"]) for i in insp.get_indexes("tags") if i["unique"]]
        check("tags.name 唯一约束", ["name"] in uniques or ("name",) in idxs,
              f"unique={uniques} uniqueIndex={idxs}")

        # 7. question_tags 联合主键
        pk = insp.get_pk_constraint("question_tags")["constrained_columns"]
        check("question_tags 联合主键", set(pk) == {"question_id", "tag_id"}, f"pk={pk}")

        # 8. 外键级联真的生效（SQLite PRAGMA 验证）
        with Session(engine) as s:
            subject = Folder(name="高等数学", level=1)
            s.add(subject)
            s.flush()
            category = Folder(name="极限与连续", level=2, parent_id=subject.id)
            s.add(category)
            s.flush()

            q = Question(folder_id=category.id, stem="求 lim(x→0) sinx/x",
                         answer="1", is_starred=True)
            tag = Tag(name="极限")
            s.add_all([q, tag])
            s.flush()
            q.tags.append(tag)

            img = QuestionImage(question_id=q.id, file_path="uploads/2026/01/01/a.jpg",
                                width=1080, height=810, size=35000)
            rec = ReviewRecord(question_id=q.id, review_count=1, interval_index=0,
                               last_review_at=datetime.now(UTC),
                               next_review_at=datetime.now(UTC), mastery_level=2)
            note = Note(title="备忘", content="记得复习", question_id=q.id)
            s.add_all([img, rec, note])
            s.commit()

            qid, tag_id, cat_id, subj_id = q.id, tag.id, category.id, subject.id

            # 关联表写入成功
            rows = s.execute(
                __import__("sqlalchemy").text(
                    "SELECT COUNT(*) FROM question_tags WHERE question_id=:q"),
                {"q": qid}).scalar()
            check("多对多关联写入", rows == 1, f"question_tags 行数={rows}")

            # 硬删题目 -> 关联表 / 图片 / 复习记录级联删除，笔记保留但 question_id 置空
            s.delete(q)
            s.commit()

            def count(sql: str, **params) -> int:
                return s.execute(__import__("sqlalchemy").text(sql), params).scalar()

            check("删题目级联删 question_tags",
                  count("SELECT COUNT(*) FROM question_tags WHERE question_id=:i", i=qid) == 0)
            check("删题目级联删 question_images",
                  count("SELECT COUNT(*) FROM question_images WHERE question_id=:i", i=qid) == 0)
            check("删题目级联删 review_records",
                  count("SELECT COUNT(*) FROM review_records WHERE question_id=:i", i=qid) == 0)
            check("删题目后 notes 保留且 question_id 置空",
                  count("SELECT COUNT(*) FROM notes") == 1
                  and count("SELECT COUNT(*) FROM notes WHERE question_id IS NULL") == 1)
            check("删题目不影响 tags", count("SELECT COUNT(*) FROM tags WHERE id=:i", i=tag_id) == 1)

            # 硬删一级文件夹 -> 级联删二级
            s.delete(s.get(Folder, subj_id))
            s.commit()
            check("删父文件夹级联删子文件夹",
                  count("SELECT COUNT(*) FROM folders WHERE id=:i", i=cat_id) == 0)

        # 9. settings 默认值
        from app.models import DEFAULT_SETTINGS, KEY_INTERVALS
        import json
        check("settings 默认 intervals=[3,7,15,30]",
              json.loads(DEFAULT_SETTINGS[KEY_INTERVALS]) == [3, 7, 15, 30],
              DEFAULT_SETTINGS[KEY_INTERVALS])

        # 10. utcnow 返回带时区时间
        from app.models import utcnow
        now = utcnow()
        check("utcnow() 返回带时区 UTC", now.tzinfo is not None and now.utcoffset().total_seconds() == 0,
              f"{now.isoformat()}")

        # 11. 导出记录表可写
        with Session(engine) as s:
            s.add(ExportRecord(range_desc="全部 · 含答案", question_count=12,
                               file_path="backups/export_20260101.pdf"))
            s.commit()
            check("export_records 可写入", s.query(ExportRecord).count() == 1)

        # 12. Setting 可写
        with Session(engine) as s:
            s.add(Setting(key="backfill_limit", value="20"))
            s.commit()
            check("settings 可写入", s.query(Setting).count() == 1)

        # 13. datetime 往返：写入 aware UTC，读回应保持 aware 且时刻不变
        #     这是"存 UTC"能否真正落地的地方（SQLite 不带时区，必须靠 Python 侧保证）
        with Session(engine) as s:
            f = Folder(name="往返测试", level=1)
            s.add(f)
            s.flush()
            q2 = Question(folder_id=f.id, stem="t")
            s.add(q2)
            s.flush()
            written = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=UTC)
            s.add(ReviewRecord(question_id=q2.id, review_count=0, interval_index=0,
                               last_review_at=written, next_review_at=written,
                               mastery_level=0))
            s.commit()
            s.expire_all()
            got = (s.query(ReviewRecord)
                   .filter(ReviewRecord.question_id == q2.id)
                   .one().last_review_at)
            aware = got.tzinfo is not None and got.utcoffset().total_seconds() == 0
            same = got.astimezone(UTC) == written if aware else False
            check("datetime 往返保持 aware UTC", aware and same,
                  f"写入 {written.isoformat()} -> 读回 {got.isoformat() if got else None}")

        # 关闭引擎，释放文件句柄，否则 Windows 上临时目录删不掉
        engine.dispose()

    print("-" * 78)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
