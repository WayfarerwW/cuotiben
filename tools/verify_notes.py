"""记事本自检（requirements.md 2.15 / 4.6）。

覆盖：
  - POST /notes 新建（标题与内容都可空）
  - GET /notes 列表，按 updated_at 倒序，返回 id/title/content/updated_at
  - GET /notes/{id} 详情
  - PUT /notes/{id} 编辑：只改传入字段；传 null 表示清空
  - DELETE /notes/{id} 软删除（记录仍在库中）
  - GET /notes/search?q= 标题与内容模糊匹配
  - 超长 content 在列表里截断、详情里完整
  - question_id 可空、可关联题目；关联到不存在的题目报 404
  - 路由顺序：/notes/search 不被 /notes/{id} 吞掉

走临时数据库，不碰 data/cuotiben.db。

运行：python tools/verify_notes.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
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
        check(label, True, f"{type(e).__name__}: {str(e)[:52]}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"抛了意外异常 {type(e).__name__}: {str(e)[:52]}")


def main() -> int:
    print("=" * 80)
    print("记事本自检")
    print("=" * 80)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["CUOTIBEN_DATABASE_URL"] = (
            f"sqlite:///{(Path(tmp) / 'notes.db').as_posix()}"
        )

        from app.database import SessionLocal, engine, init_db

        init_db()
        from app.models import Note
        from app.services import note_service as nsvc
        from app.services.note_service import (
            NoteNotFoundError,
            NoteQuestionNotFoundError,
        )

        # ============ 1. service 层 ============
        with SessionLocal() as db:
            print("\n-- 新建 --")
            n1 = nsvc.create_note(db, title="极限笔记", content="洛必达法则的适用条件")
            check("新建笔记成功", n1.id is not None and n1.deleted_at is None,
                  f"id={n1.id}")
            check("updated_at 与 created_at 均已写入",
                  n1.created_at is not None and n1.updated_at is not None)

            n_empty = nsvc.create_note(db)
            check("标题与内容都可空（允许先建空笔记）",
                  n_empty.title is None and n_empty.content is None,
                  f"id={n_empty.id}")

            n2 = nsvc.create_note(db, title="导数笔记", content="链式法则容易漏乘")
            n3 = nsvc.create_note(db, title="积分纪要", content="换元要换上下限")

            print("\n-- 列表排序 --")
            listed = nsvc.list_notes(db)
            check("列表按 updated_at 倒序（最新在前）",
                  [n.id for n in listed][:3] == [n3.id, n2.id, n_empty.id],
                  f"顺序={[n.id for n in listed]}")
            check("列表包含全部 4 条（含空笔记）", len(listed) == 4,
                  f"{len(listed)} 条")

            print("\n-- 详情 --")
            detail = nsvc.get_note(db, n1.id)
            check("详情取到正确记录", detail.title == "极限笔记")
            expect_raises("取不存在的笔记 -> NoteNotFoundError",
                          (NoteNotFoundError,), nsvc.get_note, db, 99999)

            print("\n-- 搜索（标题与内容）--")
            by_title = nsvc.search_notes(db, "导数")
            check("按标题匹配", [n.id for n in by_title] == [n2.id],
                  f"{[n.title for n in by_title]}")
            by_content = nsvc.search_notes(db, "洛必达")
            check("按内容匹配", [n.id for n in by_content] == [n1.id],
                  f"{[n.title for n in by_content]}")
            both = nsvc.search_notes(db, "换")
            check("正文命中（换元换上下限）", [n.id for n in both] == [n3.id],
                  f"{[n.title for n in both]}")
            check("无匹配返回空", nsvc.search_notes(db, "不存在的词") == [])
            check("空关键词返回空（不做全量）", nsvc.search_notes(db, "  ") == [])
            # 标题与内容都可能命中同一关键词：n1 标题含"笔记"、内容含"法则"
            check("同一关键词可命中多条（标题各自命中）",
                  {n.id for n in nsvc.search_notes(db, "笔记")} == {n1.id, n2.id},
                  f"{[n.title for n in nsvc.search_notes(db, '笔记')]}")

            print("\n-- 编辑 --")
            # 先取一份独立的 updated_at 快照再改，避免拿"已被刷新过的同一对象"比较
            before_updated = nsvc.get_note(db, n1.id).updated_at
            time.sleep(0.02)
            upd = nsvc.update_note(db, n1.id, title="极限笔记（改）",
                                   fields_to_update={"title"})
            check("只改传入字段：title 变了", upd.title == "极限笔记（改）",
                  str(upd.title))
            check("未传的 content 保持原值",
                  upd.content == "洛必达法则的适用条件", str(upd.content))
            check("编辑后 updated_at 刷新", upd.updated_at > before_updated,
                  f"{before_updated.isoformat()} -> {upd.updated_at.isoformat()}")
            check("编辑后排序生效（该笔记升到最前）",
                  nsvc.list_notes(db)[0].id == n1.id,
                  f"首条={nsvc.list_notes(db)[0].title}")

            # 显式传 None 清空字段
            cleared = nsvc.update_note(db, n1.id, content=None,
                                       fields_to_update={"content"})
            check("显式传 null 可清空 content", cleared.content is None,
                  str(cleared.content))
            check("清空 content 不影响 title", cleared.title == "极限笔记（改）")
            check("清空后按原内容搜不到",
                  nsvc.search_notes(db, "洛必达") == [],
                  f"{[n.title for n in nsvc.search_notes(db, '洛必达')]}")

            # 不传 fields_to_update 的旧语义：非 None 才更新
            old_sem = nsvc.update_note(db, n1.id, content="重新写入内容")
            check("不传 fields_to_update 时按非 None 更新",
                  old_sem.content == "重新写入内容", str(old_sem.content))
            check("重新写入后可被搜到",
                  [n.id for n in nsvc.search_notes(db, "重新")] == [n1.id])

            expect_raises("编辑不存在的笔记 -> NoteNotFoundError",
                          (NoteNotFoundError,), nsvc.update_note, db, 99999,
                          title="x", fields_to_update={"title"})

            print("\n-- 关联题目 --")
            from app.services import folder_service, question_service

            subj = folder_service.create_folder(db, "高等数学")
            cat = folder_service.create_folder(db, "极限", parent_id=subj.id)
            q = question_service.create_question(db, folder_id=cat.id, stem="关联题")
            n_linked = nsvc.create_note(db, title="关联题目的笔记", question_id=q.id)
            check("可关联题目", n_linked.question_id == q.id,
                  f"question_id={n_linked.question_id}")
            expect_raises("关联不存在的题目 -> NoteQuestionNotFoundError",
                          (NoteQuestionNotFoundError,), nsvc.create_note, db,
                          title="x", question_id=99999)

            print("\n-- 截断 --")
            long_note = nsvc.create_note(db, title="长文", content="字" * 500)
            truncated = nsvc.truncate_for_list(long_note.content)
            check("列表内容被截断到 200 字 + 省略号",
                  len(truncated) == nsvc.MAX_LIST_CONTENT + 1
                  and truncated.endswith("…"),
                  f"长度={len(truncated)}")
            check("短内容不截断", nsvc.truncate_for_list("短") == "短")
            check("None 保持 None", nsvc.truncate_for_list(None) is None)

            print("\n-- 软删除 --")
            nsvc.delete_note(db, n2.id)
            remaining = [n.id for n in nsvc.list_notes(db)]
            check("软删除后不在列表", n2.id not in remaining, f"剩 {len(remaining)} 条")
            check("软删除是置 deleted_at，记录仍在库中",
                  db.query(Note).filter_by(id=n2.id).count() == 1)
            check("软删除的笔记搜不到",
                  n2.id not in [n.id for n in nsvc.search_notes(db, "链式法则")])
            expect_raises("删除后再取详情 -> NoteNotFoundError",
                          (NoteNotFoundError,), nsvc.get_note, db, n2.id)
            expect_raises("重复删除 -> NoteNotFoundError",
                          (NoteNotFoundError,), nsvc.delete_note, db, n2.id)

        # ============ 2. HTTP 层 ============
        print("\n-- HTTP 层 --")
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            # 新建
            r = client.post("/notes", json={"title": "接口笔记", "content": "正文内容"})
            check("POST /notes -> 201", r.status_code == 201,
                  f"HTTP {r.status_code} {r.text[:100]}")
            created = r.json()

            r = client.post("/notes", json={})
            check("POST /notes 空 body 也能建（标题内容都可空）",
                  r.status_code == 201, f"HTTP {r.status_code}")

            # 列表
            r = client.get("/notes")
            check("GET /notes -> 200", r.status_code == 200)
            items = r.json()
            fields = set(items[0].keys())
            for f in ("id", "title", "content", "updated_at"):
                check(f"列表项含约定字段 {f}", f in fields,
                      f"字段={sorted(fields)}")
            check("列表按 updated_at 倒序",
                  all(items[i]["updated_at"] >= items[i + 1]["updated_at"]
                      for i in range(len(items) - 1)),
                  f"{[i['updated_at'] for i in items]}")

            # 详情
            r = client.get(f"/notes/{created['id']}")
            check("GET /notes/{id} -> 200", r.status_code == 200)
            check("详情含完整 content", r.json()["content"] == "正文内容")
            check("GET 不存在 -> 404", client.get("/notes/99999").status_code == 404)

            # 长文截断：接口层验证
            long_resp = client.post("/notes", json={"title": "长", "content": "字" * 500})
            long_id = long_resp.json()["id"]
            list_item = next(i for i in client.get("/notes").json() if i["id"] == long_id)
            check("列表里长内容被截断", len(list_item["content"]) == 201,
                  f"列表长度={len(list_item['content'])}")
            check("详情里长内容完整",
                  len(client.get(f"/notes/{long_id}").json()["content"]) == 500,
                  f"详情长度={len(client.get(f'/notes/{long_id}').json()['content'])}")

            # 搜索先于编辑：编辑会把标题/内容改掉，之后按原文就搜不到了
            # 搜索 + 路由顺序（关键：/notes/search 不能被 /notes/{id} 吞掉）
            r = client.get("/notes/search", params={"q": "正文"})
            check("GET /notes/search -> 200（未被 /{note_id} 吞掉，否则 422）",
                  r.status_code == 200, f"HTTP {r.status_code}")
            check("搜索命中内容", any(i["id"] == created["id"] for i in r.json()),
                  f"命中 {len(r.json())} 条")
            r = client.get("/notes/search", params={"q": "接口笔记"})
            check("搜索命中标题", any(i["id"] == created["id"] for i in r.json()),
                  f"命中 {len(r.json())} 条")
            r = client.get("/notes/search", params={"q": ""})
            check("空关键词返回空数组", r.json() == [], f"{r.json()}")
            r = client.get("/notes/search", params={"q": "x", "limit": 0})
            check("limit=0 越界 -> 422", r.status_code == 422, f"HTTP {r.status_code}")

            # 编辑
            r = client.put(f"/notes/{created['id']}", json={"title": "改过的标题"})
            check("PUT 只改标题 -> 200", r.status_code == 200)
            check("content 未被清空", r.json()["content"] == "正文内容",
                  str(r.json()["content"]))
            check("改标题后按新标题可搜到",
                  any(i["id"] == created["id"]
                      for i in client.get("/notes/search",
                                          params={"q": "改过的标题"}).json()))
            r = client.put(f"/notes/{created['id']}", json={"content": None})
            check("PUT 传 null 清空 content", r.json()["content"] is None,
                  str(r.json()["content"]))
            check("清空后按原内容搜不到",
                  all(i["id"] != created["id"]
                      for i in client.get("/notes/search",
                                          params={"q": "正文"}).json()))
            check("PUT 不存在 -> 404",
                  client.put("/notes/99999", json={"title": "x"}).status_code == 404)

            # 删除
            r = client.delete(f"/notes/{created['id']}")
            check("DELETE -> 200", r.status_code == 200)
            check("删除后不在列表",
                  created["id"] not in [i["id"] for i in client.get("/notes").json()])
            check("删除后详情 404",
                  client.get(f"/notes/{created['id']}").status_code == 404)
            check("删除后搜不到",
                  created["id"] not in
                  [i["id"] for i in client.get("/notes/search",
                                               params={"q": "正文"}).json()])
            check("重复删除 404",
                  client.delete(f"/notes/{created['id']}").status_code == 404)

        engine.dispose()

    print("-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
