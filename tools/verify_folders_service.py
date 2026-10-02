"""folders 业务逻辑自检（service 层，不经 HTTP）。

覆盖 requirements.md 2.2 / 4.1 与 AGENTS.md 3.4：
  - 两级结构：顶级=学科、parent_id 指向学科=大类
  - 同一父下不允许同名（含软删除后可重建同名）
  - 不允许三级嵌套
  - 树结构与 question_count 正确
  - 软删除：删学科连带子类；有题目时默认拒删，
    force=true **连同其下题目一起软删除**（requirements 2.2）
  - service 抛业务异常（不抛 HTTPException）

走临时数据库，不碰 data/cuotiben.db。

运行：python tools/verify_folders_service.py
"""

from __future__ import annotations

import os
import sys
import tempfile
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


def expect_raises(label: str, exc_types: tuple[type, ...], fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
        check(label, False, "没有抛异常")
    except exc_types as e:
        check(label, True, f"{type(e).__name__}: {str(e)[:70]}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"抛了意外异常 {type(e).__name__}: {str(e)[:70]}")


def main() -> int:
    print("=" * 78)
    print("folders service 自检")
    print("=" * 78)

    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "folders.db"
        os.environ["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"

        from app.database import SessionLocal, init_db
        from app.models import Question
        from app.services import folder_service as svc
        from app.services.folder_service import (
            DuplicateFolderNameError,
            FolderNotFoundError,
            InvalidFolderStructureError,
        )

        init_db()

        with SessionLocal() as db:
            # ---------- 创建 ----------
            math = svc.create_folder(db, "高等数学")
            check("创建顶级学科 level=1", math.level == 1 and math.parent_id is None,
                  f"id={math.id} level={math.level}")

            check("名称首尾空白被裁剪",
                  svc.create_folder(db, "  线性代数  ").name == "线性代数")

            cat = svc.create_folder(db, "极限与连续", parent_id=math.id)
            check("创建大类 level=2 且父正确",
                  cat.level == 2 and cat.parent_id == math.id,
                  f"id={cat.id} level={cat.level} parent={cat.parent_id}")

            # ---------- 唯一性 ----------
            expect_raises("顶级同名 -> DuplicateFolderNameError",
                          (DuplicateFolderNameError,), svc.create_folder, db, "高等数学")
            expect_raises("同父同名 -> DuplicateFolderNameError",
                          (DuplicateFolderNameError,), svc.create_folder, db,
                          "极限与连续", parent_id=math.id)

            other = svc.create_folder(db, "概率论")
            same_name = svc.create_folder(db, "极限与连续", parent_id=other.id)
            check("不同父下允许同名", same_name.id != cat.id,
                  f"两个「极限与连续」: {cat.id} / {same_name.id}")

            # ---------- 结构限制 ----------
            expect_raises("三级嵌套 -> InvalidFolderStructureError",
                          (InvalidFolderStructureError,), svc.create_folder, db,
                          "三级", parent_id=cat.id)
            expect_raises("父不存在 -> FolderNotFoundError",
                          (FolderNotFoundError,), svc.create_folder, db,
                          "孤儿", parent_id=99999)
            expect_raises("空名 -> InvalidFolderStructureError",
                          (InvalidFolderStructureError,), svc.create_folder, db, "   ")

            # ---------- 树 ----------
            tree = svc.list_tree(db)
            names = [t.name for t in tree]
            check("树只返回顶级学科", math.name in names and other.name in names
                  and cat.name not in names, f"顶层={names}")
            math_node = next(t for t in tree if t.id == math.id)
            check("学科节点带 children", [c.name for c in math_node.children] == ["极限与连续"],
                  f"children={[c.name for c in math_node.children]}")

            # ---------- question_count ----------
            db.add(Question(folder_id=cat.id, stem="q1"))
            db.add(Question(folder_id=cat.id, stem="q2"))
            db.add(Question(folder_id=cat.id, stem="已删", deleted_at=None))
            db.commit()
            # 再插一道软删除的题，不应计数
            from app.models.base import utcnow
            db.add(Question(folder_id=cat.id, stem="soft-deleted", deleted_at=utcnow()))
            db.commit()

            tree = svc.list_tree(db)
            math_node = next(t for t in tree if t.id == math.id)
            cat_node = next(c for c in math_node.children if c.id == cat.id)
            check("大类 question_count 只统计未删除", cat_node.question_count == 3,
                  f"插入 3 活 + 1 软删, count={cat_node.question_count}")
            check("学科 question_count 汇总子类", math_node.question_count == 3,
                  f"学科 count={math_node.question_count}")

            # ---------- 排序 ----------
            svc.update_folder(db, cat.id, sort_order=-5)
            tree = svc.list_tree(db)
            math_node = next(t for t in tree if t.id == math.id)
            check("sort_order 生效", math_node.children[0].id == cat.id,
                  f"children={[(c.id, c.sort_order) for c in math_node.children]}")

            # ---------- 重命名 ----------
            renamed = svc.update_folder(db, cat.id, name="极限与洛必达")
            check("重命名成功", renamed.name == "极限与洛必达")
            # 在同父下另建一个，才能构造真正的重名冲突
            sibling = svc.create_folder(db, "导数与微分", parent_id=math.id)
            expect_raises("重命名撞同父已有名 -> DuplicateFolderNameError",
                          (DuplicateFolderNameError,), svc.update_folder, db, cat.id,
                          name=sibling.name)
            # 改成自己原来的名字不应报冲突
            try:
                svc.update_folder(db, cat.id, name="极限与洛必达")
                check("重命名为自身当前名字不报冲突", True)
            except Exception as e:  # noqa: BLE001
                check("重命名为自身当前名字不报冲突", False, str(e)[:60])
            expect_raises("更新不存在的文件夹 -> FolderNotFoundError",
                          (FolderNotFoundError,), svc.update_folder, db, 99999, name="x")

            # ---------- 删除 ----------
            expect_raises("有题目时删除 -> InvalidFolderStructureError",
                          (InvalidFolderStructureError,), svc.delete_folder, db, math.id)

            result = svc.delete_folder(db, math.id, force=True)
            # math 自身 + 两个大类（极限与洛必达、导数与微分）= 3
            check("force=true 删学科与其子类，并返回结构化统计",
                  result["folders"] == 3,
                  f"受影响 {result['folders']} 个文件夹")
            check("**force=true 连同其下题目一起软删除**",
                  result["questions"] == 3,
                  f"连带删除 {result['questions']} 道题（应 3："
                  "该大类共 4 条记录，其中 1 条本来就是软删除的）")
            tree = svc.list_tree(db)
            check("被删学科已从树中消失",
                  math.id not in [t.id for t in tree], f"顶层={[t.name for t in tree]}")
            alive_in_cat = db.query(Question).filter(
                Question.folder_id == cat.id,
                Question.deleted_at.is_(None)).count()
            check("**该大类下已无未删除的题目**", alive_in_cat == 0,
                  f"未删除题目数={alive_in_cat}")
            all_in_cat = db.query(Question).filter(
                Question.folder_id == cat.id).count()
            check("题目仍在库里（是软删除，不是物理删除）", all_in_cat == 4,
                  f"该大类下题目总数={all_in_cat}")
            expect_raises("重复删除 -> FolderNotFoundError",
                          (FolderNotFoundError,), svc.delete_folder, db, math.id)

            # ---------- 软删除后可重建同名 ----------
            try:
                rebuilt = svc.create_folder(db, "高等数学")
                check("软删除后可重建同名学科", rebuilt.id != math.id,
                      f"新 id={rebuilt.id}，旧 id={math.id}")
            except Exception as e:  # noqa: BLE001
                check("软删除后可重建同名学科", False, str(e)[:70])

            # ---------- 删学科连带子类 ----------
            svc.delete_folder(db, rebuilt.id, force=True)
            check("删除无题目的学科正常", True)

            # list_subjects 只返回学科。
            # 此时未删除的学科：线性代数、概率论（高等数学已删，重建的也删了）
            subjects = svc.list_subjects(db)
            check("list_subjects 只含 level=1 且数量正确",
                  all(s.level == 1 for s in subjects) and len(subjects) == 2,
                  f"{[(s.name, s.level) for s in subjects]}")

        # ---------- service 不依赖 Web 框架 ----------
        # 用 AST 检查真实 import，而不是字符串搜索：模块 docstring 里
        # 就写着 "不抛 HTTPException"，字符串匹配会误报。
        import ast
        import inspect

        tree_ast = ast.parse(inspect.getsource(svc))
        imported: list[str] = []
        for node in ast.walk(tree_ast):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        banned = [m for m in imported
                  if m.startswith(("fastapi", "starlette", "uvicorn"))]
        check("service 未 import Web 框架（AST 校验）", not banned,
              f"imports={sorted(set(imported))} 违规={banned}")

        from app.database import engine

        # ---------- HTTP 层：PUT 传 name=null 必须是 422 ----------
        # name 为必填，不允许传 null（requirements.md 2.2 / 4.1）。
        # 该 422 由 schemas.FolderUpdate 的校验器产生，只有走 HTTP 才能验证。
        print("\n-- HTTP 层：PUT /folders/{id} 传 null --")
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            # 前面的用例已经建过"概率论"，这里用不会重复的名字
            subj_resp = client.post("/folders", json={"name": "HTTP 层学科"})
            check("HTTP 建学科 -> 201", subj_resp.status_code == 201,
                  f"HTTP {subj_resp.status_code} {subj_resp.text[:80]}")
            subj = subj_resp.json()
            node = client.post("/folders",
                               json={"name": "HTTP 层大类",
                                     "parent_id": subj["id"]}).json()

            r = client.put(f"/folders/{node['id']}", json={"name": None})
            check("HTTP PUT name=null -> 422（name 必填）", r.status_code == 422,
                  f"HTTP {r.status_code} {r.text[:110]}")
            check("报错信息提到 name",
                  "name" in r.text, r.text[:110])

            r = client.put(f"/folders/{node['id']}", json={"name": ""})
            check("HTTP PUT name=空串 -> 422", r.status_code == 422,
                  f"HTTP {r.status_code}")

            r = client.put(f"/folders/{node['id']}", json={"name": "新名字"})
            check("HTTP PUT name=正常值 -> 200", r.status_code == 200,
                  f"HTTP {r.status_code}")
            check("重命名结果正确", r.json()["name"] == "新名字", r.json()["name"])

            # name=null 被拒后名称不应被改动
            client.put(f"/folders/{node['id']}", json={"name": None})
            r = client.get("/folders/tree")
            still = [c for n in r.json() if n["id"] == subj["id"]
                     for c in n["children"] if c["id"] == node["id"]]
            check("name=null 被拒后名称未被改动",
                  bool(still) and still[0]["name"] == "新名字",
                  str(still[0]["name"]) if still else "未找到节点")

            # 只传 sort_order 时 name 不动（未传 ≠ 传 null）
            r = client.put(f"/folders/{node['id']}", json={"sort_order": 5})
            check("HTTP 只传 sort_order -> 200 且 name 不动",
                  r.status_code == 200 and r.json()["name"] == "新名字",
                  f"HTTP {r.status_code} name={r.json().get('name')!r}")

        engine.dispose()

    print("-" * 78)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
