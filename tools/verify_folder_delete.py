"""删文件夹连带删题 的语义自检（requirements.md 2.2）。

## 这条规则为什么存在

原来 `force=true` 只是"仅软删除文件夹，题目保持原状不动"。后果是题目变成
**半死状态**：列表里还看得见、侧边栏却没有任何大类可点、编辑时「所属大类」
下拉是空的（保存必 422），而它们的图片既不可见、也不被孤儿扫描识别
（孤儿判定只看 `question.deleted_at`，不看题目所属文件夹是否已删）。

真实发生过：用户把「高等数学」下的大类全删了，导致 4 道活题挂在已删除的
大类下、4 张图永久留在磁盘上既看不到也清不掉。

所以改成：`force=true` = 用户已确认这一整块都不要了 →
**连同其下题目一起软删除**。题目一被软删除，它的图片立刻变成孤儿，
现有「清理孤儿图片」按钮就能回收 —— 不需要在删除流程里同步删文件
（那样既慢又不可逆）。

## 覆盖

- 不带 force：有题目时 409 拒绝（安全阀保留）
- force=true：文件夹与其下题目**都被软删除**
- **其下图片随之变成孤儿**，且能被 `cleanup_orphans` 真正清掉
- 复习队列里不再出现这些题
- 文件夹计数归零、树里消失
- 学科 → 连带其下所有大类与题目
- 无题目时 force 与不带 force 效果一致
- 返回结构化的删除统计

全程用临时库 + 临时 uploads（绝不能碰真实数据）。
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 必须在 import 任何 app.* **之前**把 uploads 隔离到临时目录，
# 否则本自检会动到真实 uploads/（这是踩过的坑）。
# ---------------------------------------------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="verify_folder_delete_"))
_UPLOADS = _TMP / "uploads"
_UPLOADS.mkdir(parents=True, exist_ok=True)
os.environ["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{(_TMP / 't.db').as_posix()}"
os.environ["CUOTIBEN_UPLOADS_DIR"] = str(_UPLOADS)
os.environ["CUOTIBEN_BACKUPS_DIR"] = str(_TMP / "backups")
os.environ["GIT_SYNC_ENABLED"] = "0"

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


def _jpeg(color: tuple = (30, 60, 200)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (60, 40), color).save(buf, "JPEG", quality=88)
    return buf.getvalue()


def main() -> int:  # noqa: C901
    from app.database import SessionLocal, init_db
    from app.models import Question, QuestionImage
    from app.services import folder_service, image_service, question_service
    from app.services.folder_service import InvalidFolderStructureError

    if image_service.UPLOADS_DIR.resolve() != _UPLOADS.resolve():
        print("!! 致命：UPLOADS_DIR 未隔离到临时目录，拒绝继续")
        return 3
    expect("uploads 已隔离到临时目录（哨兵通过）", True,
           str(image_service.UPLOADS_DIR))

    init_db()          # 临时库是空的，先建表

    db = SessionLocal()
    try:
        # ---------------- 造数据 ----------------
        # 学科 → 大类A（2 题，各带 1 图）/ 大类B（1 题，无图）
        subj = folder_service.create_folder(db, "高等数学")
        cat_a = folder_service.create_folder(db, "线性代数", parent_id=subj.id)
        cat_b = folder_service.create_folder(db, "立体几何", parent_id=subj.id)

        def mk_q(folder, stem: str) -> Question:
            q = Question(folder_id=folder.id, stem=stem, answer="答案",
                         is_starred=False, mastery_status="still_wrong",
                         sort_order=0)
            db.add(q)
            db.commit()
            return q

        q1 = mk_q(cat_a, "题一")
        q2 = mk_q(cat_a, "题二")
        q3 = mk_q(cat_b, "题三")

        # 给 q1 / q2 各挂一张真图（走 save_upload，落在日期分片目录里）
        for q, color in ((q1, (200, 60, 60)), (q2, (60, 200, 60))):
            rel, _abs = image_service.save_upload(_jpeg(color), ".jpg")
            question_service.update_question(
                db, q.id, images=[{"url": rel, "kind": "stem"}],
                fields_to_update={"images"})
        db.commit()

        q1_img = image_service.absolute_path_of(
            db.query(QuestionImage).filter(
                QuestionImage.question_id == q1.id).one().file_path)
        q2_img = image_service.absolute_path_of(
            db.query(QuestionImage).filter(
                QuestionImage.question_id == q2.id).one().file_path)
        expect("造好 3 道题、2 张真图",
               q1_img.is_file() and q2_img.is_file())

        # 让题目进复习队列：打勾产生复习记录，再把到期时间回拨到"今天"
        # —— 这样"题目是否出现在队列里"才真的取决于它有没有被软删除。
        import datetime as dt

        from app.models import ReviewRecord
        from app.services import review_service

        review_service.check(db, q1.id, mastery=None)
        review_service.check(db, q2.id, mastery=None)
        review_service.check(db, q3.id, mastery=None)
        past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        for rec in db.query(ReviewRecord).all():
            rec.next_review_at = past
            rec.deleted_at = None
        db.commit()
        db.expire_all()

        due_before = {i.question_id for i in review_service.list_today(db)[0]}
        expect("（准备）三道题都已进入今日复习队列",
               {q1.id, q2.id, q3.id} <= due_before, sorted(due_before))

        # ---------------- 安全阀保留 ----------------
        section("不带 force：有题目时仍然拒绝")
        try:
            folder_service.delete_folder(db, cat_a.id)
            expect("有题目时不带 force 应被拒绝", False, "竟然删成功了")
        except InvalidFolderStructureError as exc:
            expect("有题目时不带 force 应被拒绝", True, str(exc)[:52])
        except Exception as exc:  # noqa: BLE001
            expect("有题目时不带 force 应被拒绝", False,
                   f"抛了 {type(exc).__name__}")
        expect("被拒绝后文件夹仍活着",
               folder_service.get_folder(db, cat_a.id).deleted_at is None)

        # ---------------- 核心：force 连带删题 ----------------
        section("force=true：文件夹与其下题目一起软删除")
        result = folder_service.delete_folder(db, cat_a.id, force=True)
        expect("返回结构化结果（含 folders/questions 计数）",
               isinstance(result, dict) and "folders" in result
               and "questions" in result, result)
        expect("报告删了 1 个文件夹", result.get("folders") == 1, result)
        expect("**报告删了 2 道题目**", result.get("questions") == 2, result)

        db.expire_all()
        for q, name in ((q1, "题一"), (q2, "题二")):
            got = db.get(Question, q.id)
            expect(f"**{name} 已被软删除**", got.deleted_at is not None,
                   f"deleted_at={got.deleted_at}")
        q3_after = db.get(Question, q3.id)
        expect("另一个大类下的题**不受影响**",
               q3_after.deleted_at is None)

        expect("文件夹已从树里消失",
               cat_a.id not in [t.id for t in folder_service.list_tree(db)])
        subj_after = folder_service.list_tree(db)
        expect("学科的 question_count 已减少",
               (subj_after[0].question_count if subj_after else 0) == 1,
               subj_after[0].question_count if subj_after else None)

        # ---------------- 图片变成孤儿且可清理 ----------------
        section("其下图片变成孤儿，并可被清理")
        orphans = {o.file_path for o in image_service.scan_orphans(db)}
        expect("**题一的图片被识别为孤儿**",
               str(q1_img.relative_to(image_service.UPLOADS_DIR).as_posix())
               in {p.replace("uploads/", "", 1) for p in orphans}
               or any(q1_img.name in o for o in orphans),
               sorted(orphans))
        expect("**题二的图片被识别为孤儿**",
               any(q2_img.name in o for o in orphans), sorted(orphans))

        cleaned = image_service.cleanup_orphans(db)
        expect("清理报告删除了 2 张图", cleaned["deleted"] == 2, cleaned["deleted"])
        expect("**题一的图片文件已从磁盘删除**", not q1_img.is_file())
        expect("**题二的图片文件已从磁盘删除**", not q2_img.is_file())

        # ---------------- 复习队列 ----------------
        section("复习队列不再包含被删的题")
        remaining_q = db.query(Question).filter(
            Question.deleted_at.is_(None)).count()
        expect("库里只剩 1 道活题", remaining_q == 1, remaining_q)

        due_after = {i.question_id for i in review_service.list_today(db)[0]}
        expect("**今日复习队列里没有已删的题**",
               q1.id not in due_after and q2.id not in due_after,
               sorted(due_after))
        expect("未被删的题仍在今天队列里（对照）",
               q3.id in due_after, sorted(due_after))

        # ---------------- 无题目时的删除 ----------------
        section("无题目的文件夹：force 与不带 force 等效")
        cat_empty = folder_service.create_folder(db, "空大类", parent_id=subj.id)
        r1 = folder_service.delete_folder(db, cat_empty.id)
        expect("空文件夹不带 force 也能删", r1["folders"] == 1, r1)
        expect("空文件夹报告删了 0 道题", r1["questions"] == 0, r1)

        # ---------------- 学科连带其下大类与题目 ----------------
        section("删学科：连带其下所有大类与题目")
        result2 = folder_service.delete_folder(db, subj.id, force=True)
        expect("报告删了 2 个文件夹（学科 + 剩下的大类）",
               result2["folders"] == 2, result2)
        expect("**报告删了 1 道题（题三）**", result2["questions"] == 1, result2)
        db.expire_all()
        expect("题三也已被软删除",
               db.get(Question, q3.id).deleted_at is not None)
        expect("已无任何活着的文件夹",
               folder_service.list_tree(db) == [])
    finally:
        db.close()

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
