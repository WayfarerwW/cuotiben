"""验证软删除与 folders 部分唯一索引的配合行为。

背景：folders 上有一个部分唯一索引 uq_folders_parent_name_active
（parent_id, name）WHERE deleted_at IS NULL，用于实现需求 2.2
"同一父下不允许同名"。必须确认它不会误伤软删除场景——
删掉一个"线性代数"后，应该还能再建一个同名的大类。

运行：python tools/verify_softdelete.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import create_db_engine  # noqa: E402
from app.models import Base, Folder  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 74)
    print("软删除 × 部分唯一索引 验证")
    print("=" * 74)

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "softdel.db"
        engine = create_db_engine(f"sqlite:///{db.as_posix()}")
        Base.metadata.create_all(engine)

        with Session(engine) as s:
            subj = Folder(name="高等数学", level=1)
            s.add(subj)
            s.flush()
            s.add(Folder(name="线性代数", level=1, parent_id=subj.id))
            s.commit()
            check("正常插入两个文件夹", True, "高等数学 / 线性代数")

        # 同名同父、都未删除 -> 应被唯一索引拦截
        with Session(engine) as s:
            subj = s.query(Folder).filter_by(name="高等数学").one()
            try:
                s.add(Folder(name="线性代数", level=1, parent_id=subj.id))
                s.commit()
                check("同名同父被唯一索引拦截", False, "竟然插入成功了")
            except IntegrityError:
                s.rollback()
                check("同名同父被唯一索引拦截", True, "IntegrityError，符合预期")

        # 软删除后应能重建同名
        with Session(engine) as s:
            child = s.query(Folder).filter_by(name="线性代数").one()
            child.deleted_at = datetime.now(UTC)
            s.commit()
            check("子文件夹软删除", True, f"deleted_at={child.deleted_at.isoformat()}")

        with Session(engine) as s:
            subj = s.query(Folder).filter_by(name="高等数学").one()
            try:
                s.add(Folder(name="线性代数", level=1, parent_id=subj.id))
                s.commit()
                check("软删除后可重建同名大类", True, "部分唯一索引未误伤")
            except IntegrityError as e:
                s.rollback()
                check("软删除后可重建同名大类", False, f"被拦截: {str(e)[:80]}")

        # 不同父下可以同名
        with Session(engine) as s:
            another = Folder(name="概率论", level=1)
            s.add(another)
            s.flush()
            subj = s.query(Folder).filter_by(name="高等数学").one()
            s.add(Folder(name="线性代数", level=1, parent_id=another.id))
            s.commit()
            check("不同父下可同名", True, "两个'线性代数'分别挂在不同学科下")

        # 查询默认只看未删除（应用层约定，这里演示 filter 生效）
        # 此时共有 5 行：高等数学、概率论、软删除的线性代数、
        # 重建的线性代数、挂在概率论下的线性代数 -> 未删除 4 行
        with Session(engine) as s:
            active = s.query(Folder).filter(Folder.deleted_at.is_(None)).count()
            total = s.query(Folder).count()
            check("软删除过滤查询可用", active == 4 and total == 5,
                  f"未删除={active} 总数={total}")

        engine.dispose()

    print("-" * 74)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, dd in failed:
        print(f"  FAILED: {n} {dd}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
