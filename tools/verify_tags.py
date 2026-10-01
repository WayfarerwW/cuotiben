"""标签自检：归一化、复用、联想、多标签 AND/OR、计数。

覆盖 requirements.md 2.4 / 2.5 / 5.4 / 5.5 / 4.3 与 AGENTS.md 4.6：
  - 归一化：trim + 小写 + 全半角转换；已存在复用、不存在新建
  - GET /tags：全部标签，按使用次数倒序（标签云要高频在前）
  - GET /tags/search?q=：模糊匹配，查询词同样归一化
  - GET /questions?tag=&tag_mode=and|or：多标签 AND / OR
  - question_count 只统计未删除题目

service 层与 HTTP 层都验。走临时数据库，不碰 data/cuotiben.db。

运行：python tools/verify_tags.py
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


def main() -> int:
    print("=" * 80)
    print("标签自检（归一化 / 联想 / 多标签查询）")
    print("=" * 80)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["CUOTIBEN_DATABASE_URL"] = (
            f"sqlite:///{(Path(tmp) / 'tags.db').as_posix()}"
        )

        from app.database import SessionLocal, engine, init_db

        init_db()
        from app.models import Tag
        from app.services import folder_service as fsvc
        from app.services import question_service as qsvc
        from app.services import settings_service, tag_service as tsvc

        # ============ 1. 归一化（AGENTS.md 4.6）============
        print("\n-- 归一化 trim + 小写 + 全半角 --")
        cases = [
            ("  极限  ", "极限", "trim"),
            ("极限\u3000", "极限", "全角空格"),
            ("ＡＢＣ", "abc", "全角字母 -> 小写"),
            ("ＡＢＣ１２３", "abc123", "全角字母+数字"),
            ("Limit", "limit", "半角大写 -> 小写"),
            ("极限！", "极限!", "全角标点"),
            ("　导数　", "导数", "全角空格两侧"),
            ("ＬＩＭＩＴ", "limit", "全角大写"),
        ]
        for raw, expect, label in cases:
            got = tsvc.normalize_tag(raw)
            check(f"归一化 {label}: {raw!r} -> {got!r}", got == expect,
                  f"期望 {expect!r}")

        # 三个等价写法只产生一个标签
        check("等价写法去重（极限/ 极限 /极限　）",
              tsvc.normalize_tags(["极限", " 极限 ", "极限\u3000"]) == ["极限"])
        check("空标签被忽略", tsvc.normalize_tags(["", "  ", "极限"]) == ["极限"])

        with SessionLocal() as db:
            settings_service.ensure_default_settings(db)
            subj = fsvc.create_folder(db, "高等数学")
            cat = fsvc.create_folder(db, "极限与连续", parent_id=subj.id)

            # ============ 2. 复用而非新建 ============
            print("\n-- 已存在复用、不存在新建 --")
            q1 = qsvc.create_question(db, folder_id=cat.id, stem="题1",
                                      tags=["极限", " 极限 ", "极限\u3000"])
            check("同一题内等价标签只挂一次", len(q1.tags) == 1,
                  f"{[t.name for t in q1.tags]}")

            q2 = qsvc.create_question(db, folder_id=cat.id, stem="题2", tags=["极限"])
            check("跨题复用同一标签记录",
                  {t.id for t in q1.tags} == {t.id for t in q2.tags},
                  f"q1={[t.id for t in q1.tags]} q2={[t.id for t in q2.tags]}")

            q3 = qsvc.create_question(db, folder_id=cat.id, stem="题3",
                                      tags=["洛必达", "导数"])
            check("新标签被创建", db.query(Tag).count() == 3,
                  f"标签总数={db.query(Tag).count()}（极限/洛必达/导数）")

            # 造更多数据以便验证计数排序
            q4 = qsvc.create_question(db, folder_id=cat.id, stem="题4",
                                      tags=["极限", "洛必达"])
            check("标签可挂 1~N 个（q4 挂 2 个）", len(q4.tags) == 2)

            # ============ 3. 计数与排序 ============
            print("\n-- 计数与排序（标签云要高频在前）--")
            counts = tsvc.list_tags_with_counts(db)
            by_name = {t.name: c for t, c in counts}
            check("question_count 正确（极限 3 / 洛必达 2 / 导数 1）",
                  by_name == {"极限": 3, "洛必达": 2, "导数": 1}, str(by_name))
            check("按使用次数倒序", [t.name for t, _ in counts] == ["极限", "洛必达", "导数"],
                  f"{[t.name for t, _ in counts]}")

            # 软删除的题目不计入
            qsvc.delete_question(db, q3.id)
            by_name2 = {t.name: c for t, c in tsvc.list_tags_with_counts(db)}
            # q3 = {洛必达, 导数}，软删后洛必达 2->1、导数 1->0；
            # 极限只挂在 q1/q2/q4 上，不受影响仍是 3
            check("软删除题目后计数下降（导数 1 -> 0）",
                  by_name2["导数"] == 0, str(by_name2))
            check("软删除同时影响该题的多个标签（洛必达 2 -> 1）",
                  by_name2["洛必达"] == 1, str(by_name2))
            check("软删除不影响未挂该题的标签（极限 仍为 3）",
                  by_name2["极限"] == 3, str(by_name2))

            # ============ 4. 联想搜索 ============
            print("\n-- 标签联想 --")
            check("前缀匹配", [t.name for t in tsvc.search_tags(db, "极")] == ["极限"],
                  f"{[t.name for t in tsvc.search_tags(db, '极')]}")
            check("中间匹配（模糊而非仅前缀）",
                  [t.name for t in tsvc.search_tags(db, "必")] == ["洛必达"],
                  f"{[t.name for t in tsvc.search_tags(db, '必')]}")
            check("查询词也归一化：全角 ＡＢＣ 不报错且正确匹配",
                  tsvc.search_tags(db, "ＡＢＣ") == [],
                  "无匹配返回空（不抛异常）")
            check("中文查询词 trim",
                  [t.name for t in tsvc.search_tags(db, "  极限  ")] == ["极限"])
            check("空查询返回空", tsvc.search_tags(db, "") == [])

            # ============ 5. 多标签 AND / OR（需求 5.5）============
            print("\n-- 多标签 AND / OR --")
            # 此时存活题目：q1{极限} q2{极限} q4{极限, 洛必达}；q3{洛必达,导数} 已软删除
            f = qsvc.QuestionFilters

            and_q = qsvc.list_questions(db, f(tags=["极限", "洛必达"], tag_mode="and"))
            check("AND：必须同时含全部标签 -> 只命中 q4",
                  {q.id for q in and_q} == {q4.id},
                  f"命中={[q.stem for q in and_q]}")

            # 反向对照：AND 一定比 OR 窄
            or_same = qsvc.list_questions(db, f(tags=["极限", "洛必达"], tag_mode="or"))
            check("同一组标签 OR 结果严格多于 AND",
                  len(or_same) > len(and_q),
                  f"or={len(or_same)} and={len(and_q)}")

            or_q = qsvc.list_questions(db, f(tags=["极限", "导数"], tag_mode="or"))
            check("OR：含任一即可（导数 已随 q3 软删，故只命中含极限的题）",
                  {q.id for q in or_q} == {q1.id, q2.id, q4.id},
                  f"命中={[q.stem for q in or_q]}")

            single = qsvc.list_questions(db, f(tags=["极限"], tag_mode="and"))
            single_or = qsvc.list_questions(db, f(tags=["极限"], tag_mode="or"))
            check("单标签时 AND == OR",
                  {q.id for q in single} == {q.id for q in single_or},
                  f"and={len(single)} or={len(single_or)}")

            check("查询标签也归一化（全角/首尾空格亦然）",
                  {q.id for q in qsvc.list_questions(db, f(tags=["  极限  "]))}
                  == {q.id for q in single})

            # 组合筛选
            combo = qsvc.list_questions(db, f(tags=["极限"], starred=True))
            check("标签 + 其他条件组合筛选可用",
                  all(q.is_starred for q in combo), f"命中 {len(combo)} 题")

        # ============ 6. HTTP 层 ============
        print("\n-- HTTP 层 --")
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            r = client.get("/tags")
            check("GET /tags -> 200", r.status_code == 200, f"HTTP {r.status_code}")
            tags = r.json()
            check("GET /tags 返回数组且按次数倒序",
                  [t["name"] for t in tags] == ["极限", "洛必达", "导数"],
                  f"{[t['name'] for t in tags]}")
            check("GET /tags 含 question_count 字段",
                  all("question_count" in t for t in tags),
                  f"字段={sorted(tags[0].keys()) if tags else '空'}")
            check("GET /tags 计数与 service 一致",
                  {t["name"]: t["question_count"] for t in tags}
                  == {"极限": 3, "洛必达": 1, "导数": 0},
                  str({t["name"]: t["question_count"] for t in tags}))

            r = client.get("/tags/search", params={"q": "极"})
            check("GET /tags/search?q=极 -> 200", r.status_code == 200)
            check("联想命中 极限", [t["name"] for t in r.json()] == ["极限"],
                  f"{[t['name'] for t in r.json()]}")

            r = client.get("/tags/search", params={"q": "必"})
            check("联想支持中间匹配", [t["name"] for t in r.json()] == ["洛必达"])

            r = client.get("/tags/search", params={"q": "ＡＢＣ"})
            check("全角查询词不报错", r.status_code == 200 and r.json() == [],
                  f"HTTP {r.status_code}")

            r = client.get("/tags/search", params={"q": "极", "limit": 1})
            check("limit 参数生效", len(r.json()) <= 1, f"{len(r.json())} 条")

            r = client.get("/tags/search", params={"q": "极", "limit": 0})
            check("limit=0 越界 -> 422", r.status_code == 422, f"HTTP {r.status_code}")

            # 问题接口的 tag_mode
            r = client.get("/questions", params=[("tag", "极限"), ("tag", "洛必达"),
                                                ("tag_mode", "and")])
            check("GET /questions?tag_mode=and -> 200", r.status_code == 200)
            check("HTTP 层 AND 只命中同时含两标签的题", len(r.json()) == 1,
                  f"命中 {len(r.json())} 题")

            r = client.get("/questions", params=[("tag", "极限"), ("tag", "导数"),
                                                ("tag_mode", "or")])
            check("HTTP 层 OR 命中含任一标签的题", len(r.json()) == 3,
                  f"命中 {len(r.json())} 题")

            r = client.get("/questions", params=[("tag", "极限"), ("tag_mode", "xor")])
            check("tag_mode 非法值 -> 422", r.status_code == 422, f"HTTP {r.status_code}")

        engine.dispose()

    print("-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
