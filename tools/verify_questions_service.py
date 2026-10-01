"""题目业务逻辑自检（service 层，不经 HTTP）。

覆盖 requirements.md 2.3 / 2.5 / 2.13 / 2.14 / 4.2 / 5.5 与 AGENTS.md 4.6：
  - 标签归一化：trim + 小写 + 全半角转换后去重；已存在复用、不存在新建
  - 创建时自动生成首条 review_record（interval_index=0，next_review_at = now()+3天）
  - 题目只能挂二级文件夹
  - 列表筛选：folder_id / tag(AND|OR) / keyword / starred / mastery
  - star / unstar / mastery 切换
  - 软删除后不出现在列表

走临时数据库，不碰 data/cuotiben.db。

运行：python tools/verify_questions_service.py
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


def expect_raises(label: str, exc_types: tuple[type, ...], fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
        check(label, False, "没有抛异常")
    except exc_types as e:
        check(label, True, f"{type(e).__name__}: {str(e)[:60]}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"抛了意外异常 {type(e).__name__}: {str(e)[:60]}")


def main() -> int:
    print("=" * 80)
    print("questions service 自检")
    print("=" * 80)

    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "questions.db"
        os.environ["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"

        from app.database import SessionLocal, engine, init_db
        from app.models import MASTERY_MASTERED, MASTERY_STILL_WRONG, Tag
        from app.services import folder_service as fsvc
        from app.services import question_service as qsvc
        from app.services import settings_service, tag_service
        from app.services.folder_service import InvalidFolderStructureError
        from app.services.question_service import QuestionNotFoundError
        from app.services.tag_service import InvalidTagError

        init_db()

        # ================= 标签归一化（AGENTS.md 4.6）=================
        print("\n-- 标签归一化 --")
        cases = [
            ("  极限  ", "极限", "trim"),
            ("极限\u3000", "极限", "全角空格"),
            ("ＡＢＣ", "abc", "全角字母 + 小写"),
            ("ＬＩＭＩＴ", "limit", "全角大写"),
            ("Limit", "limit", "半角大写"),
            ("极限！", "极限!", "全角标点"),
            ("　导数　", "导数", "全角空格两侧"),
        ]
        for raw, expect, label in cases:
            got = tag_service.normalize_tag(raw)
            check(f"归一化 {label}: {raw!r} -> {got!r}", got == expect, f"期望 {expect!r}")

        got = tag_service.normalize_tags(["极限", " 极限 ", "极限\u3000", "极限！", "极限", ""])
        check("批量归一化去重", got == ["极限", "极限!"], f"{got}")

        expect_raises("超长标签 -> InvalidTagError", (InvalidTagError,),
                      tag_service.normalize_tags, ["字" * 101])

        # ================= 准备文件夹 =================
        with SessionLocal() as db:
            settings_service.ensure_default_settings(db)
            subj = fsvc.create_folder(db, "高等数学")
            cat = fsvc.create_folder(db, "极限与连续", parent_id=subj.id)
            cat2 = fsvc.create_folder(db, "导数与微分", parent_id=subj.id)

            # ================= 创建题目 + 首条 review_record =================
            print("\n-- 创建题目 --")
            before = datetime.now(UTC)
            q1 = qsvc.create_question(
                db, folder_id=cat.id, stem="求 lim(x→0) sinx/x", answer="1",
                tags=["极限", " 极限 ", "等价无穷小"], is_starred=True,
                images=["uploads/2026/10/01/a.jpg"],
            )
            after = datetime.now(UTC)
            check("创建题目成功", q1.id is not None and q1.folder_id == cat.id,
                  f"id={q1.id}")

            # 注意：sorted() 按 Unicode 码点排序，'极'(U+6781) < '等'(U+7B49)，
            # 所以不能凭语感把期望写成 ['等价无穷小', '极限']。用 set 比较更稳。
            check("标签归一化后去重（3 个输入 -> 2 个标签）",
                  {t.name for t in q1.tags} == {"极限", "等价无穷小"},
                  f"actual={sorted(t.name for t in q1.tags)}")

            check("图片已关联", len(q1.images) == 1 and q1.images[0].file_path.endswith("a.jpg"),
                  f"{[i.file_path for i in q1.images]}")

            rec = qsvc.current_review_record(db, q1.id)
            check("自动生成首条 review_record", rec is not None)
            check("首条 interval_index=0", rec.interval_index == 0, f"{rec.interval_index}")
            check("首条 review_count=0", rec.review_count == 0, f"{rec.review_count}")
            check("首条 last_review_at 为空", rec.last_review_at is None)
            expect = before.replace(microsecond=0)
            delta_days = (rec.next_review_at - expect).total_seconds() / 86400
            check("next_review_at ≈ now() + 3 天（默认 intervals[0]）",
                  2.99 <= delta_days <= 3.01,
                  f"next_review_at={rec.next_review_at.isoformat()} 距 now {delta_days:.4f} 天")
            check("next_review_at 带时区", rec.next_review_at.tzinfo is not None)
            check("next_review_at 在 now 之后（未被后置）",
                  rec.next_review_at > after or rec.next_review_at > before)

            # 纯图片题目（题干答案都为空）
            q_img = qsvc.create_question(db, folder_id=cat.id, images=["uploads/x.jpg"])
            check("题干与答案可为空（纯图片题）",
                  q_img.stem is None and q_img.answer is None and len(q_img.images) == 1)

            # ================= 层级校验 =================
            print("\n-- 层级与存在性校验 --")
            expect_raises("题目挂一级学科 -> InvalidFolderStructureError",
                          (InvalidFolderStructureError,), qsvc.create_question, db,
                          folder_id=subj.id, stem="x")
            from app.services.folder_service import FolderNotFoundError
            expect_raises("folder 不存在 -> FolderNotFoundError",
                          (FolderNotFoundError,), qsvc.create_question, db,
                          folder_id=99999, stem="x")

            # ================= 标签复用 =================
            print("\n-- 标签复用 --")
            q2 = qsvc.create_question(db, folder_id=cat.id, stem="第二题",
                                      tags=["极限", "洛必达"])
            tag_names = {t.name for t in q2.tags}
            total_tags = db.query(Tag).count()
            # 到此刻标签表应只累积了 3 个：极限、等价无穷小、洛必达
            check("已存在标签被复用而非新建", "极限" in tag_names and total_tags == 3,
                  f"tags={sorted(tag_names)} 标签表总数={total_tags}")
            check("新标签被创建", "洛必达" in tag_names)

            # ================= 筛选 =================
            print("\n-- 列表筛选 --")
            q3 = qsvc.create_question(db, folder_id=cat2.id, stem="导数定义",
                                      answer="极限的另一种形式", tags=["导数"])
            q4 = qsvc.create_question(db, folder_id=cat2.id, stem="洛必达法则",
                                      mastery_status=MASTERY_MASTERED, tags=["极限", "洛必达"])

            f = qsvc.QuestionFilters
            all_q = qsvc.list_questions(db, f())
            check("无筛选返回全部未删除", len(all_q) == 5, f"{len(all_q)} 条")

            by_folder = qsvc.list_questions(db, f(folder_id=cat2.id))
            check("按 folder_id 筛选", {q.id for q in by_folder} == {q3.id, q4.id},
                  f"{[q.stem for q in by_folder]}")

            and_q = qsvc.list_questions(db, f(tags=["极限", "洛必达"], tag_mode="and"))
            check("多标签 AND（须同时含全部）",
                  {q.id for q in and_q} == {q2.id, q4.id},
                  f"{[q.stem for q in and_q]}")

            or_q = qsvc.list_questions(db, f(tags=["极限", "导数"], tag_mode="or"))
            check("多标签 OR（含任一即可）",
                  {q.id for q in or_q} == {q1.id, q2.id, q3.id, q4.id},
                  f"{[q.stem for q in or_q]}")

            single = qsvc.list_questions(db, f(tags=["导数"]))
            check("单标签 AND == OR", {q.id for q in single} == {q3.id})

            kw = qsvc.list_questions(db, f(keyword="洛必达"))
            # keyword 只搜题干与答案，不搜标签：q2 的题干是「第二题」、标签才是「洛必达」，
            # 所以只应命中题干含「洛必达法则」的 q4。
            check("关键词匹配题干", {q.id for q in kw} == {q4.id},
                  f"{[q.stem for q in kw]}")

            kw2 = qsvc.list_questions(db, f(keyword="另一种形式"))
            check("关键词也匹配答案", {q.id for q in kw2} == {q3.id},
                  f"{[q.stem for q in kw2]}")

            starred = qsvc.list_questions(db, f(starred=True))
            check("只看重点", {q.id for q in starred} == {q1.id}, f"{[q.stem for q in starred]}")

            mastered = qsvc.list_questions(db, f(mastery=MASTERY_MASTERED))
            check("按正误筛选 mastered", {q.id for q in mastered} == {q4.id})

            still = qsvc.list_questions(db, f(mastery=MASTERY_STILL_WRONG))
            check("按正误筛选 still_wrong", len(still) == 4, f"{len(still)} 条")

            combo = qsvc.list_questions(db, f(folder_id=cat.id, tags=["极限"], starred=True))
            check("组合筛选（folder + tag + starred）",
                  {q.id for q in combo} == {q1.id}, f"{[q.stem for q in combo]}")

            check("列表带 folder_name", all(q.folder_name for q in all_q),
                  f"{sorted({q.folder_name for q in all_q})}")

            # ================= 详情 / 编辑 =================
            print("\n-- 详情与编辑 --")
            detail = qsvc.get_question(db, q1.id)
            check("详情含标签与图片",
                  len(detail.tags) == 2 and len(detail.images) == 1,
                  f"tags={[t.name for t in detail.tags]} images={len(detail.images)}")

            updated = qsvc.update_question(db, q1.id, stem="改过的题干",
                                           tags=["新标签"],
                                           images=["uploads/new.jpg"])
            check("编辑题干生效", updated.stem == "改过的题干")
            check("编辑标签整体替换", [t.name for t in updated.tags] == ["新标签"],
                  f"{[t.name for t in updated.tags]}")
            check("编辑图片整体替换",
                  [i.file_path for i in updated.images] == ["uploads/new.jpg"],
                  f"{[i.file_path for i in updated.images]}")

            partial = qsvc.update_question(db, q1.id, answer="只改答案")
            check("部分更新不动其他字段（stem 保持、tags 不变）",
                  partial.answer == "只改答案" and partial.stem == "改过的题干"
                  and [t.name for t in partial.tags] == ["新标签"])

            expect_raises("编辑不存在的题目 -> QuestionNotFoundError",
                          (QuestionNotFoundError,), qsvc.update_question, db, 99999,
                          stem="x")

            # ================= star / mastery =================
            print("\n-- 重点与正误 --")
            check("初始为已重点", qsvc.get_question(db, q1.id).is_starred)
            unstarred = qsvc.set_starred(db, q1.id, False)
            check("取消重点", unstarred.is_starred is False)
            starred2 = qsvc.set_starred(db, q1.id, True)
            check("重新标记重点", starred2.is_starred is True)

            check("初始 still_wrong", q2.mastery_status == MASTERY_STILL_WRONG)
            toggled = qsvc.toggle_mastery(db, q2.id)
            check("toggle -> mastered", toggled.mastery_status == MASTERY_MASTERED,
                  toggled.mastery_status)
            toggled2 = qsvc.toggle_mastery(db, q2.id)
            check("再 toggle -> still_wrong", toggled2.mastery_status == MASTERY_STILL_WRONG,
                  toggled2.mastery_status)
            set_ok = qsvc.set_mastery(db, q2.id, MASTERY_MASTERED)
            check("直接设置 mastered", set_ok.mastery_status == MASTERY_MASTERED)
            expect_raises("非法 mastery -> QuestionError",
                          (qsvc.QuestionError,), qsvc.set_mastery, db, q2.id, "bogus")

            # ================= 软删除 =================
            print("\n-- 软删除 --")
            qsvc.delete_question(db, q3.id)
            after_del = qsvc.list_questions(db, f())
            check("软删除后不在列表", q3.id not in {q.id for q in after_del},
                  f"剩 {len(after_del)} 条")
            check("软删除是置 deleted_at，不是物理删除",
                  db.query(type(q3)).filter_by(id=q3.id).count() == 1)
            expect_raises("删除后再取详情 -> QuestionNotFoundError",
                          (QuestionNotFoundError,), qsvc.get_question, db, q3.id)
            expect_raises("重复删除 -> QuestionNotFoundError",
                          (QuestionNotFoundError,), qsvc.delete_question, db, q3.id)

            # ================= 标签计数 =================
            print("\n-- 标签统计 --")
            counts = dict((t.name, c) for t, c in tag_service.list_tags_with_counts(db))
            check("标签计数排除已软删除题目", counts.get("导数", 0) == 0,
                  f"导数 -> {counts.get('导数')}（该题已软删除）")
            check("标签计数正确", counts.get("洛必达", 0) == 2,
                  f"counts={counts}")

            search = tag_service.search_tags(db, "极")
            check("标签联想搜索", any(t.name == "极限" for t in search),
                  f"{[t.name for t in search]}")
            search_full = tag_service.search_tags(db, "ＡＢＣ")
            check("联想搜索词也归一化（全角输入不炸）", search_full == [],
                  "无匹配返回空")

        engine.dispose()

    print("-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
