"""数据库层 + Pydantic schema 自检。

覆盖本步交付：
  1. app.database 提供 Base / engine / SessionLocal / get_db
  2. get_db 是可用的 FastAPI 依赖（会话能开关）
  3. app.main 启动时建表 + 写入 settings 默认值（幂等、不覆盖用户改动）
  4. app.schemas 关键模型可校验、可拒绝非法输入、能由 ORM 对象构造
  5. /health 可用（证明 app 能正常起）

全部走临时数据库，不碰 data/cuotiben.db。

运行：python tools/verify_db_schemas.py
"""

from __future__ import annotations

import os
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

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 80)
    print("数据库层 + Pydantic schema 自检")
    print("=" * 80)

    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "verify.db"
        # 必须在 import app.main 之前设好，让全局 engine 指向临时库
        os.environ["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"

        # ---------- 1. app.database 对外接口 ----------
        from app.database import (  # noqa: E402
            Base,
            SessionLocal,
            engine,
            get_db,
            init_db,
        )

        check("app.database 提供 Base/engine/SessionLocal/get_db", True,
              f"Base={Base.__name__} engine={engine.name} url={engine.url.database}")

        # ---------- 2. get_db 作为依赖可用 ----------
        gen = get_db()
        session = next(gen)
        try:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
            usable = True
        except Exception as e:  # noqa: BLE001
            usable = False
            err = str(e)[:80]
        finally:
            try:
                next(gen)  # 关闭会话
            except StopIteration:
                pass
        check("get_db 产出可用会话并在结束后关闭", usable,
              "SELECT 1 成功" if usable else f"失败: {err}")

        # ---------- 3. init_db 建表 + 外键开关 ----------
        init_db()
        from sqlalchemy import inspect
        tables = sorted(inspect(engine).get_table_names())
        check("init_db 建出九张表", len(tables) == 9, f"{tables}")

        with engine.connect() as conn:
            from sqlalchemy import text
            fk_on = conn.execute(text("PRAGMA foreign_keys")).scalar()
        check("每个连接已开启 PRAGMA foreign_keys", fk_on == 1, f"foreign_keys={fk_on}")

        # init_db 幂等
        try:
            init_db()
            check("init_db 幂等（可重复调用）", True)
        except Exception as e:  # noqa: BLE001
            check("init_db 幂等（可重复调用）", False, str(e)[:80])

        # ---------- 4. settings 默认值初始化 ----------
        from app.services.settings_service import ensure_default_settings
        from app.models import (
            DEFAULT_BACKFILL_LIMIT,
            DEFAULT_BACKFILL_RESET_DAYS,
            DEFAULT_INTERVALS,
            Setting,
        )
        from sqlalchemy import select

        with SessionLocal() as db:
            created = ensure_default_settings(db)
        check("首次启动写入 3 个默认配置", len(created) == 3, f"新增: {created}")

        with SessionLocal() as db:
            kv = {s.key: s.value for s in db.scalars(select(Setting)).all()}
        check("默认值内容正确",
              kv.get("intervals") == "[3, 7, 15, 30]"
              and kv.get("backfill_limit") == "20"
              and kv.get("backfill_reset_days") == "14",
              f"{kv}")

        # 幂等：再跑一次不应新增
        with SessionLocal() as db:
            created2 = ensure_default_settings(db)
        check("重复初始化不新增、不覆盖", created2 == [], f"新增: {created2}")

        # 用户改过的值不能被重置
        with SessionLocal() as db:
            row = db.scalars(select(Setting).where(Setting.key == "backfill_limit")).one()
            row.value = "5"
            db.commit()
        with SessionLocal() as db:
            ensure_default_settings(db)
        with SessionLocal() as db:
            row = db.scalars(select(Setting).where(Setting.key == "backfill_limit")).one()
            kept = row.value
        check("已存在的用户配置不被默认值覆盖", kept == "5", f"backfill_limit={kept}")

        # ---------- 5. SettingsOut 类型解析 ----------
        from app.schemas import (
            BackfillResetRequest,
            FolderCreate,
            FolderTree,
            NoteCreate,
            QuestionCreate,
            QuestionOut,
            ReviewCountOut,
            ReviewItemOut,
            SettingsOut,
            SettingsUpdate,
        )

        check("SettingsOut 默认值与 models 常量一致",
              SettingsOut().intervals == DEFAULT_INTERVALS
              and SettingsOut().backfill_limit == DEFAULT_BACKFILL_LIMIT
              and SettingsOut().backfill_reset_days == DEFAULT_BACKFILL_RESET_DAYS,
              f"{SettingsOut().model_dump()}")

        # 非法输入必须被拒
        for label, model, payload in (
            ("intervals 非递增被拒", SettingsUpdate, {"intervals": [3, 7, 7, 30]}),
            ("intervals 含 0 被拒", SettingsUpdate, {"intervals": [0, 7, 15, 30]}),
            ("intervals 长度不为 4 被拒", SettingsUpdate, {"intervals": [3, 7]}),
            ("backfill_limit 为负被拒", SettingsUpdate, {"backfill_limit": -1}),
            ("文件夹空名被拒", FolderCreate, {"name": ""}),
            ("题干为空字符串的题目允许", QuestionCreate, {"folder_id": 1}),
        ):
            try:
                model(**payload)
                rejected = False
            except Exception:  # noqa: BLE001
                rejected = True
            expect_reject = "被拒" in label or "长度" in label or "为负" in label or "空名" in label
            ok = rejected == expect_reject
            check(label, ok, "拒绝" if rejected else "接受")

        # mastery_status 枚举
        try:
            QuestionCreate(folder_id=1, mastery_status="bogus")  # type: ignore[arg-type]
            check("非法 mastery_status 被拒", False, "竟然通过了")
        except Exception:  # noqa: BLE001
            check("非法 mastery_status 被拒", True)

        # ---------- 6. 由 ORM 对象构造响应模型 ----------
        from app.models import Folder, Question, QuestionImage, Tag

        with SessionLocal() as db:
            f = Folder(name="高等数学", level=1)
            db.add(f)
            db.flush()
            cat = Folder(name="极限与连续", level=2, parent_id=f.id)
            db.add(cat)
            db.flush()
            q = Question(folder_id=cat.id, stem="求极限", answer="1", is_starred=True)
            tag = Tag(name="极限")
            db.add_all([q, tag])
            db.flush()
            q.tags.append(tag)
            db.add(QuestionImage(question_id=q.id, file_path="uploads/a.jpg",
                                 width=1080, height=810, size=35000))
            db.commit()

            q_out = QuestionOut.model_validate(q)
            check("QuestionOut 由 ORM 对象构造（含标签/图片）",
                  q_out.id == q.id and len(q_out.tags) == 1 and len(q_out.images) == 1,
                  f"tags={[t.name for t in q_out.tags]} images={len(q_out.images)}")

            tree = FolderTree.model_validate(f)
            # children 是真实 relationship，from_attributes 会自动填充整棵树
            check("FolderTree 递归构建子树",
                  tree.name == "高等数学" and len(tree.children) == 1
                  and tree.children[0].name == "极限与连续",
                  f"children={[c.name for c in tree.children]}")

            # 二级节点（叶子）不应再有 children，且 parent 反向关系不会造成循环
            leaf = FolderTree.model_validate(cat)
            check("叶子节点 children 为空且无循环",
                  leaf.name == "极限与连续" and leaf.children == [],
                  f"leaf.children={leaf.children}")

            # datetime 必须是带时区的 ISO 8601（AGENTS.md 3.1）
            iso = q_out.created_at.isoformat()
            aware = q_out.created_at.tzinfo is not None
            check("响应模型 datetime 带时区", aware, f"created_at={iso}")

        # ---------- 7. 今日队列 / 打勾 schema ----------
        item = ReviewItemOut(
            question_id=1, folder_id=2, folder_name="极限与连续", stem="x",
            answer=None, is_starred=True, mastery_status="still_wrong",
            next_review_at=datetime.now(UTC), interval_index=0,
            overdue_days=15, is_backlog=True, is_overdue=True,
        )
        check("ReviewItemOut 支持积压区标记", item.is_backlog and item.overdue_days == 15)

        cnt = ReviewCountOut(count=3)
        check("ReviewCountOut 只回 count（前端现有契约）", cnt.count == 3, f"{cnt.model_dump()}")

        req = BackfillResetRequest(reset_days=14, spread=True)
        check("BackfillResetRequest 默认/显式值正确",
              req.reset_days == 14 and req.spread is True)

        note = NoteCreate(title="备忘", content="复习洛必达")
        check("NoteCreate 可空字段可用", note.question_id is None)

        # ---------- 8. 真实启动一次应用（子进程 + 独立临时库）----------
        # 不能在进程内 import app.main 来测：engine 在 import 时就绑定了当时的
        # CUOTIBEN_DATABASE_URL，本进程已经 import 过 app.database，改成临时库也无效。
        # 因此直接以子进程启动 uvicorn，走真实启动路径。
        import json
        import socket
        import subprocess
        import time
        import urllib.request

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        app_db = Path(tmp) / "app_start.db"
        env = dict(os.environ)
        env["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{app_db.as_posix()}"
        env["PYTHONPATH"] = str(ROOT)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace",
        )
        health = None
        try:
            deadline = time.time() + 40
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=2
                    ) as resp:
                        health = json.loads(resp.read().decode("utf-8"))
                        break
                except Exception:  # noqa: BLE001
                    time.sleep(0.4)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        check("真实启动 uvicorn 后 /health 可用", bool(health),
              f"{health} (port={port})")

        # 关键：确认启动落在临时库上，且表与默认配置都写好了
        if health:
            ok_path = str(app_db).replace("\\", "/") in str(health.get("db_path", "")).replace("\\", "/")
            check("启动使用的数据库是预期文件", ok_path, f"db_path={health.get('db_path')}")

        if app_db.exists():
            import sqlite3

            conn = sqlite3.connect(app_db)
            try:
                tbls = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )]
                kv = dict(conn.execute("SELECT key, value FROM settings"))
            finally:
                conn.close()
            check("uvicorn 启动流程建出九张表", len(tbls) == 9, f"{tbls}")
            check("uvicorn 启动流程写入默认配置",
                  kv.get("intervals") == "[3, 7, 15, 30]"
                  and kv.get("backfill_limit") == "20"
                  and kv.get("backfill_reset_days") == "14",
                  f"{kv}")
        else:
            check("uvicorn 启动流程建出九张表", False, f"库文件不存在: {app_db}")

        # ---------- 9. OpenAPI 可生成（进程内，只关心 schema 生成能力）----------
        from app.main import app

        try:
            spec = app.openapi()
            paths = sorted(spec.get("paths", {}).keys())
            check("OpenAPI 可生成", "paths" in spec, f"路径数={len(paths)}: {paths}")
        except Exception as e:  # noqa: BLE001
            check("OpenAPI 可生成", False, f"{type(e).__name__}: {str(e)[:90]}")

        engine.dispose()

    print("-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
