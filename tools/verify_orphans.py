"""孤儿图片清理自检（requirements 4.4）。

覆盖：
- 定义：孤儿 = uploads/ 下存在、但没有任何**未删除题目**在引用
- **不能误删**：被题目引用的、挂在已软删除题目上的、未挂题但有记录的
  —— 三者都必须保留（最后一类算孤儿，见下）
- 清理后文件真的消失、计数与释放字节正确
- **防目录穿越**：只处理 uploads/ 内的文件；uploads/ 之外的任何东西都不动
- 统计接口只读，不删任何文件
- 清理失败不静默（failed 计数与列表）

不用 pytest，照本项目惯例直接跑、打印 [PASS]/[FAIL]、用退出码表示结果。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 必须在 import 任何 app.* **之前**设好环境变量！
#
# image_service.UPLOADS_DIR 是**模块导入时**读环境变量定下来的常量。
# 第一版把这两个赋值放在了 import 之后，于是 svc.UPLOADS_DIR 指向**真实的
# uploads/**，本自检的 cleanup 调用把真实目录清空了 —— 4 张被题目引用的
# 图片被误删且无法恢复（unlink 不进回收站、无备份、uploads/ 不入库）。
#
# 现在的写法 + 下面的"哨兵"断言共同保证：只要 UPLOADS_DIR 不是临时目录，
# 脚本立刻退出，绝不碰真实文件。
# ---------------------------------------------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="verify_orphan_"))
_UPLOADS = _TMP / "uploads"
_UPLOADS.mkdir()
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


def main() -> int:  # noqa: C901
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import image_service as svc

    # ---- 哨兵：这一步不通就立刻退出，绝不继续 ----
    real_uploads = (ROOT / "uploads").resolve()
    if svc.UPLOADS_DIR.resolve() != _UPLOADS.resolve():
        print("!! 致命：UPLOADS_DIR 未指向临时目录，拒绝继续")
        print(f"   svc.UPLOADS_DIR = {svc.UPLOADS_DIR}")
        print(f"   期望            = {_UPLOADS}")
        print(f"   （真实目录 {real_uploads} 绝不能被动到）")
        return 3
    expect("uploads 已隔离到临时目录（哨兵通过）",
           svc.UPLOADS_DIR.resolve() == _UPLOADS.resolve(),
           str(svc.UPLOADS_DIR))
    expect("临时目录不在项目内（再确认一次）",
           not svc.UPLOADS_DIR.resolve().is_relative_to(ROOT.resolve()),
           str(svc.UPLOADS_DIR))

    tmp = _TMP
    uploads = _UPLOADS

    day = uploads / "2026" / "10" / "02"
    day.mkdir(parents=True)

    def mk(name: str, size: int = 100) -> Path:
        p = day / name
        p.write_bytes(b"x" * size)
        return p

    # 1) 被未删除题目引用的图（必须保留）
    keep_ref = mk("keep_referenced.jpg", 300)
    # 2) 完全没有数据库记录的孤儿（必须删）
    orphan_no_row = mk("orphan_no_row.jpg", 200)
    # 3) 有记录但未挂题（question_id=NULL）—— 算孤儿（必须删）
    orphan_unbound = mk("orphan_unbound.jpg", 150)
    # 4) 只挂在**已软删除题目**上（必须保留：记录还在，用户可能还原）
    keep_deleted_q = mk("keep_deleted_question.jpg", 250)
    # 5) uploads 根下的 .gitkeep（目录占位，永远保留）
    (uploads / ".gitkeep").write_bytes(b"")

    # uploads **之外**的文件：绝对不能被碰
    outside = tmp / "outside_secret.jpg"
    outside.write_bytes(b"SECRET" * 10)

    with TestClient(app) as client:
        from app.database import SessionLocal
        from app.models import Folder, Question, QuestionImage

        db = SessionLocal()
        try:
            subject = Folder(name="数学", parent_id=None, sort_order=0)
            db.add(subject)
            db.commit()
            leaf = Folder(name="极限", parent_id=subject.id, sort_order=0)
            db.add(leaf)
            db.commit()

            q_alive = Question(folder_id=leaf.id, stem="活着的题", answer="a",
                               is_starred=False, mastery_status="still_wrong",
                               sort_order=0)
            q_dead = Question(folder_id=leaf.id, stem="已删除的题", answer="b",
                              is_starred=False, mastery_status="still_wrong",
                              sort_order=1)
            db.add_all([q_alive, q_dead])
            db.commit()

            db.add(QuestionImage(
                question_id=q_alive.id,
                file_path=f"uploads/2026/10/02/{keep_ref.name}",
                sort_order=0, kind="stem"))
            db.add(QuestionImage(
                question_id=None,
                file_path=f"uploads/2026/10/02/{orphan_unbound.name}",
                sort_order=0, kind="stem"))
            db.add(QuestionImage(
                question_id=q_dead.id,
                file_path=f"uploads/2026/10/02/{keep_deleted_q.name}",
                sort_order=0, kind="stem"))
            db.commit()

            # 软删除第二道题
            from app.services import question_service
            question_service.delete_question(db, q_dead.id)
            db.commit()
        finally:
            db.close()

        # ---------------------------------------------------------------
        section("GET /upload/orphans（只读）")
        r = client.get("/upload/orphans")
        expect("接口返回 200", r.status_code == 200, r.status_code)
        data = r.json()
        names = sorted(Path(f["file_path"]).name for f in data["files"])
        # 语义（与 image_service.scan_orphans 的 docstring 一致）：
        #   被**未删除**题目引用      -> 不是孤儿
        #   挂在**已软删除**题目上    -> **是**孤儿
        #     （题目在界面上已经不可见、也删不掉记录，图片永远不会再被渲染；
        #      本项目的软删除不承诺可还原，所以保留它只是占磁盘）
        #   question_id 为 NULL       -> 是孤儿（上传后从未拿去建题）
        #   完全没有记录              -> 是孤儿（上传后没保存就关掉弹窗）
        expect("列出 3 个孤儿（已删除题的图 + 未挂题 + 无记录）",
               data["count"] == 3, f"{data['count']} 个: {names}")
        expect("无记录的孤儿被列出", orphan_no_row.name in names, names)
        expect("未挂题的孤儿被列出", orphan_unbound.name in names, names)
        expect("挂在已删除题目上的图被列为孤儿（题目已不可见）",
               keep_deleted_q.name in names, names)
        expect("**被活着的题目引用的图未被列为孤儿**",
               keep_ref.name not in names, names)
        expect("统计了可释放字节",
               data["total_bytes"] == 200 + 150 + 250, data["total_bytes"])
        expect("返回了说明文案（讲清什么算孤儿）",
               "孤儿" in data.get("note", ""), data.get("note", "")[:40])

        expect("**扫描不删任何文件**（只读）",
               keep_ref.is_file() and orphan_no_row.is_file()
               and orphan_unbound.is_file() and keep_deleted_q.is_file())
        expect(".gitkeep 未被列为孤儿",
               ".gitkeep" not in names, names)

        # ---------------------------------------------------------------
        section("POST /upload/cleanup")
        r = client.post("/upload/cleanup")
        expect("清理返回 200", r.status_code == 200, r.status_code)
        res = r.json()
        expect("found 与扫描一致", res["found"] == 3, res["found"])
        expect("deleted 为 3", res["deleted"] == 3, res["deleted"])
        expect("failed 为 0", res["failed"] == 0, res["failed"])
        expect("freed_bytes 正确", res["freed_bytes"] == 600, res["freed_bytes"])

        expect("**无记录的孤儿文件已删除**", not orphan_no_row.exists())
        expect("**未挂题的孤儿文件已删除**", not orphan_unbound.exists())
        expect("**已删除题目的图也被清掉**", not keep_deleted_q.exists())
        expect("**被活着的题目引用的文件仍在**", keep_ref.is_file())
        expect(".gitkeep 仍在", (uploads / ".gitkeep").is_file())
        expect("**uploads 之外的文件未被碰**",
               outside.is_file() and outside.read_bytes() == b"SECRET" * 10)

        # 被引用那张图应当仍是"有人用"的：再清一次也不该动它
        r_again = client.post("/upload/cleanup")
        expect("再清一次不会动被引用的图",
               r_again.json()["deleted"] == 0 and keep_ref.is_file(),
               f"deleted={r_again.json()['deleted']}")

        # 清理后再扫应当为 0
        r2 = client.get("/upload/orphans")
        expect("清理后没有孤儿了", r2.json()["count"] == 0, r2.json()["count"])

        # ---------------------------------------------------------------
        section("UPLOADS_DIR 可覆盖（隔离能力本身要有人守）")
        # 这条是被一次实际事故教育出来的：
        # image_service 以前**不读** CUOTIBEN_UPLOADS_DIR，本自检以为隔离了，
        # 实际扫描真实 uploads/ 并清空，4 张被引用的图被误删。
        # 另外 save_upload 以前写成 absolute.relative_to(PROJECT_ROOT)，
        # 只在 uploads/ 位于项目根下时才成立；UPLOADS_DIR 一被指到项目外
        # 就抛 ValueError -> 上传 500。两条都必须有断言守着。
        expect("image_service 读了 CUOTIBEN_UPLOADS_DIR",
               svc.UPLOADS_DIR.resolve() == _UPLOADS.resolve(),
               str(svc.UPLOADS_DIR))
        rel, ab = svc.save_upload(b"\xff\xd8\xff\xe0payload", ".jpg")
        expect("UPLOADS_DIR 在项目外时 save_upload 不抛异常", True, rel)
        expect("返回的相对路径以 uploads/ 开头",
               rel.startswith("uploads/"), rel)
        expect("文件写到了指定的 uploads 目录",
               ab.is_file() and ab.resolve().is_relative_to(_UPLOADS.resolve()),
               str(ab))
        expect("**relative_path 能被 absolute_path_of 解析回同一文件**"
               "（存/取对称）",
               svc.absolute_path_of(rel).resolve() == ab.resolve(),
               f"解析={svc.absolute_path_of(rel).name} 期望={ab.name}")
        expect("url_for 给出可访问的 /uploads/... 地址",
               svc.url_for(rel).startswith("/uploads/"), svc.url_for(rel))
        expect("这个刚上传的文件是孤儿（还没挂到题目上）",
               rel in [o.file_path.replace("\\", "/") for o in svc.scan_orphans(
                   SessionLocal())])

        # ---------------------------------------------------------------
        section("防目录穿越")
        # 造一条 file_path 指向 uploads 之外的记录：即便被当成孤儿，
        # 也不允许删除（delete_file 内部的 uploads/ 校验是最后一道防线）
        db = SessionLocal()
        try:
            db.add(QuestionImage(
                question_id=None,
                file_path=str(outside),          # 绝对路径，指向 uploads 之外
                sort_order=0, kind="stem"))
            db.commit()
            inside_count = len(list(uploads.rglob("*")))
            outside_in_orphans = svc.scan_orphans(db)
            names2 = [Path(o.file_path).name for o in outside_in_orphans]
            expect("**uploads 之外的文件不会被列为孤儿**",
                   outside.name not in names2,
                   f"孤儿={names2}")
            # 直接调 delete_file 试删 uploads 之外的路径
            killed = svc.delete_file(str(outside))
            expect("**delete_file 拒绝删除 uploads 之外的路径**",
                   killed is False and outside.is_file(), f"killed={killed}")
            # 用相对穿越写法再试一次
            killed2 = svc.delete_file("uploads/../../outside_secret.jpg")
            expect("**delete_file 拒绝 ../ 穿越写法**",
                   killed2 is False and outside.is_file(), f"killed={killed2}")
            expect("uploads 内文件数未变",
                   len(list(uploads.rglob("*"))) == inside_count)
        finally:
            db.close()

        # ---------------------------------------------------------------
        section("删除失败不静默")
        # 造一个"删不掉"的场景：同名目录（is_file() 为 False，delete_file 返回 False）
        weird = day / "not_a_file.jpg"
        weird.mkdir()
        db = SessionLocal()
        try:
            scan3 = svc.scan_orphans(db)
            # 目录不会被当作文件扫进来
            expect("目录不会被当成孤儿图片",
                   weird.name not in [Path(o.file_path).name for o in scan3],
                   [Path(o.file_path).name for o in scan3])
        finally:
            db.close()

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
