"""PDF 导出端到端验证（requirements.md 2.16 / 4.7）。

不用 pytest，照本项目惯例直接跑、打印 [PASS]/[FAIL]、用退出码表示结果。

覆盖：
- 五个 scope 各自取题是否正确（含 tags 的 AND 语义、manual 的顺序与不存在 id）
- with_answer 开关真的产生/省略答案区，且答案区另起一页
- 中文不是方框（从 PDF 里把文字取回来比对，这是最硬的证据）
- 题目不跨页切断（模板属性 + 多题跨页场景）
- 文件名含日期与范围、Content-Disposition 的中文编码
- 图片被写进 PDF
- 空结果报错而不是产出空白 PDF
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import _pdf_probe as P  # noqa: E402

OK = FAIL = 0
FAILED: list[str] = []


def expect(label: str, cond: bool, detail: object = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        FAILED.append(label)
    text = "" if detail == "" else f"  ({detail})"
    print(("[PASS] " if cond else "[FAIL] ") + label + text)


def section(title: str) -> None:
    print(f"\n-- {title} --")


def main() -> int:  # noqa: C901 - 线性用例清单，拆函数反而难读
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Folder, Question, Tag
    from app.schemas import ExportPdfRequest
    from app.services import export_service

    tmp = Path(tempfile.mkdtemp(prefix="verify_export_"))
    engine = create_engine(f"sqlite:///{(tmp / 'v.db').as_posix()}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    # ---------------- 造数据 ----------------
    subject = Folder(name="高等数学", parent_id=None, sort_order=0)
    other = Folder(name="线性代数", parent_id=None, sort_order=1)
    db.add_all([subject, other])
    db.commit()

    # 标签名刻意选"只出现在标签里"的词：若用题干里已有的词（如"洛必达"），
    # include_tags 开关就测不出来 —— 第一版正是踩了这个坑。
    tag_a, tag_b, tag_c = Tag(name="重要极限"), Tag(name="可导条件"), Tag(name="非零行")
    db.add_all([tag_a, tag_b, tag_c])
    db.commit()

    def mkq(folder, stem, answer, tags, *, starred=False, order=0,
            mastery="still_wrong"):
        q = Question(folder_id=folder.id, stem=stem, answer=answer,
                     is_starred=starred, mastery_status=mastery, sort_order=order)
        q.tags = list(tags)
        db.add(q)
        db.commit()
        return q

    q1 = mkq(subject, "求极限 lim(x→0) sin(x)/x", "答案为 1，用重要极限。", [tag_a])
    mkq(subject, "洛必达法则的适用条件是什么", "0/0 或 ∞/∞ 型且可导。",
        [tag_a, tag_b], order=1, starred=True)
    q3 = mkq(subject, "求 y=x^3 的导数", "y' = 3x²。", [tag_b], order=2,
             mastery="mastered")
    mkq(other, "求矩阵 A 的秩", "行简化后非零行数。", [tag_c])
    mkq(other, "<p>第一行<br>第二行 &amp; 符号</p>", "<b>加粗答案</b>",
        [tag_c], order=1)

    now = dt.datetime(2026, 2, 14, 10, 30, tzinfo=dt.timezone.utc)

    # ---------------- scope: folder ----------------
    section("scope=folder")
    res = export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=subject.id, with_answer=True),
        now=now)
    expect("文件夹范围取到该文件夹全部题目", res.question_count == 3,
          f"{res.question_count} 题")
    expect("PDF 头合法", res.pdf[:5] == b"%PDF-")
    expect("文件名含日期", "20260214" in res.filename, res.filename)
    expect("文件名含范围标识", "按文件夹" in res.filename, res.filename)
    expect("文件名以 .pdf 结尾", res.filename.endswith(".pdf"))

    text = P.extract_text(res.pdf)
    expect("题干中文可提取「洛必达」", "洛必达" in text)
    expect("答案中文可提取「重要极限」", "重要极限" in text)
    expect("含答案时有答案区标题", "答案与解析" in text)
    expect("页眉含范围", "按文件夹" in text)
    expect("页脚含页码", re.search(r"第\s*1\s*/\s*\d+\s*页", text) is not None)
    expect("富文本标签未原样出现在 PDF 里", "<p>" not in text and "<br>" not in text)

    # 富文本/实体还原要用"线性代数"那份导出验证：
    # 带 <p>/&amp; 的题在 other 文件夹里，subject 的导出件不含它。
    other_export = export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=other.id, with_answer=False),
        now=now)
    t_other = P.extract_text(other_export.pdf)
    expect("HTML 实体已还原为 &（不是 &amp;）",
           "符号" in t_other and "&amp;" not in t_other)
    expect("<br> 变成换行而不是被删掉",
           "第一行" in t_other and "第二行" in t_other)

    expect("字体已嵌入", P.has_embedded_font(res.pdf))
    expect("嵌入的是 Noto Sans SC（非回退字体）",
          any("Noto-Sans-SC" in n or "NotoSansSC" in n for n in P.font_names(res.pdf)),
          P.font_names(res.pdf))
    expect("没有出现方框替换符 U+FFFD", "\ufffd" not in text)

    # ---------------- with_answer 开关 ----------------
    section("with_answer 开关")
    no_ans = export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=subject.id, with_answer=False),
        now=now)
    t2 = P.extract_text(no_ans.pdf)
    expect("不含答案时没有答案区标题", "答案与解析" not in t2)
    expect("不含答案时仍含题干", "洛必达" in t2)
    expect("含答案比不含答案多出页数（答案另起一页）",
          P.page_count(res.pdf) > P.page_count(no_ans.pdf),
          f"含 {P.page_count(res.pdf)} 页 / 不含 {P.page_count(no_ans.pdf)} 页")

    # ---------------- include_tags 开关 ----------------
    section("include_tags 开关")
    with_tags = export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=subject.id,
                             with_answer=False, include_tags=True), now=now)
    without_tags = export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=subject.id,
                             with_answer=False, include_tags=False), now=now)
    expect("含标签时 PDF 里出现标签名「重要极限」",
          "重要极限" in P.extract_text(with_tags.pdf))
    expect("不含标签时标签文字消失",
          "重要极限" not in P.extract_text(without_tags.pdf))

    # ---------------- scope: tags（AND 语义）----------------
    section("scope=tags（多标签 AND）")
    both = export_service.export_pdf(
        db, ExportPdfRequest(scope="tags", tags=["重要极限", "可导条件"], with_answer=False),
        now=now)
    expect("两标签 AND 只命中同时含两者的题", both.question_count == 1,
          f"{both.question_count} 题（应 1）")
    one = export_service.export_pdf(
        db, ExportPdfRequest(scope="tags", tags=["重要极限"], with_answer=False), now=now)
    expect("单标签命中 2 题", one.question_count == 2, f"{one.question_count} 题")
    expect("标签范围文件名含「按标签」", "按标签" in one.filename, one.filename)

    # ---------------- scope: starred ----------------
    section("scope=starred")
    st = export_service.export_pdf(
        db, ExportPdfRequest(scope="starred", with_answer=False), now=now)
    expect("重点范围只取重点题", st.question_count == 1, f"{st.question_count} 题")
    expect("重点题在 PDF 里带星标", "★" in P.extract_text(st.pdf))

    # ---------------- scope: manual ----------------
    section("scope=manual")
    man = export_service.export_pdf(
        db, ExportPdfRequest(scope="manual", question_ids=[q3.id, q1.id],
                             with_answer=False), now=now)
    expect("手动勾选取到 2 题", man.question_count == 2, f"{man.question_count} 题")
    t_man = P.extract_text(man.pdf)
    # 传入顺序是 [导数题, 极限题]，导出件里第 1 题应当是导数题
    first_no = t_man.find("第 1 题")
    expect("手动顺序按传入顺序（第 1 题是导数题）",
          first_no != -1 and t_man.find("导数", first_no) != -1
          and t_man.find("导数", first_no) < t_man.find("第 2 题"),
          "按传入顺序")
    try:
        export_service.export_pdf(
            db, ExportPdfRequest(scope="manual", question_ids=[99999]), now=now)
        expect("不存在的 id 报错", False, "没有抛异常")
    except export_service.ExportScopeError as exc:
        expect("不存在的 id 报 ExportScopeError", True, str(exc)[:36])

    # ---------------- scope: review_queue ----------------
    section("scope=review_queue")
    from app.services import review_service

    # 造一道"已到期"的题。注意：光打勾不会让它进今日队列 ——
    # 打勾把 next_review_at 推到 3 天后，队列反而是空的。
    # 第一版就是这么写的，于是这里一路报"该范围内没有题目"。
    review_service.check(db, q1.id, mastery=3)
    rec = review_service.current_record(db, q1.id)
    expect("打勾后能取到当前复习记录", rec is not None)
    if rec is not None:
        # UTCDateTime 需要 datetime 对象，不能塞 ISO 字符串
        rec.next_review_at = now - dt.timedelta(days=1)
        db.add(rec)
        db.commit()
        due = review_service.count_due(db, now=now)
        expect("该题已进入待复习队列", due >= 1, f"到期 {due} 题")

    queue = export_service.export_pdf(
        db, ExportPdfRequest(scope="review_queue", with_answer=False), now=now)
    expect("今日待复习范围可导出", queue.question_count >= 1,
          f"{queue.question_count} 题")
    expect("待复习范围文件名含「今日待复习」", "今日待复习" in queue.filename,
          queue.filename)

    # ---------------- 空结果 ----------------
    section("空结果")
    empty_folder = Folder(name="空学科", parent_id=None, sort_order=9)
    db.add(empty_folder)
    db.commit()
    try:
        export_service.export_pdf(
            db, ExportPdfRequest(scope="folder", folder_id=empty_folder.id), now=now)
        expect("空范围抛错而不是产出空白 PDF", False, "没有抛异常")
    except export_service.ExportScopeError as exc:
        expect("空范围抛 ExportScopeError", True, str(exc)[:28])

    # ---------------- 模板属性 ----------------
    section("题目不跨页切断 / 版式")
    tpl = Path("app/templates/export_pdf.html").read_text(encoding="utf-8")
    expect("模板有 page-break-inside: avoid（旧名）",
          "page-break-inside: avoid" in tpl)
    expect("模板有 break-inside: avoid（新名）", "break-inside: avoid" in tpl)
    expect("题目块 .q 用了 avoid",
          re.search(r"\.q\s*\{[^}]*break-inside:\s*avoid", tpl, re.S) is not None)
    expect("答案块 .a 用了 avoid",
          re.search(r"\.a\s*\{[^}]*break-inside:\s*avoid", tpl, re.S) is not None)
    expect("答案区另起一页",
          "break-before: page" in tpl or "page-break-before: always" in tpl)
    expect("有页眉与页脚", "@top-center" in tpl and "@bottom-center" in tpl)
    expect("用 @font-face 引中文字体（非系统字体）",
          "@font-face" in tpl and "font_uri" in tpl)
    expect("图片自适应 max-width: 100%",
          re.search(r"\.q__images img\s*\{[^}]*max-width:\s*100%", tpl, re.S) is not None)

    # ---------------- 多题跨页 ----------------
    section("多题跨页")
    long_folder = Folder(name="多题", parent_id=None, sort_order=8)
    db.add(long_folder)
    db.commit()
    for i in range(60):
        mkq(long_folder, f"第 {i + 1} 道长题干 " + "内容" * 30, f"答案 {i + 1}",
            [tag_a], order=i)
    many = export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=long_folder.id,
                             with_answer=False), now=now)
    pages = P.page_count(many.pdf)
    expect("60 题生成多页 PDF", pages >= 3, f"{pages} 页")
    t_many = P.extract_text(many.pdf)
    expect("题号连续（第 1 题与第 60 题都在）",
          "第 1 题" in t_many and "第 60 题" in t_many)
    expect("没有半截题号", "第 题" not in t_many)
    expect("每道题都出现了（60 个题号）",
          len(re.findall(r"第 \d+ 题", t_many)) >= 60,
          f"{len(re.findall(r'第..?d+..?题', t_many))} 个")

    # ---------------- 图片 ----------------
    section("图片")
    uploads = Path("uploads")
    uploads.mkdir(exist_ok=True)
    img = uploads / "_verify_export_tmp.jpg"
    try:
        from PIL import Image

        Image.new("RGB", (60, 40), (120, 160, 220)).save(img, "JPEG")
        from app.services import question_service

        # register_image 只落一条尚未挂题的图片行；挂到题目上要走
        # update_question(images=[...])（它内部用 _sync_images 复用该行）
        question_service.register_image(
            db, file_path="/uploads/_verify_export_tmp.jpg")
        question_service.update_question(
            db, q1.id, images=["/uploads/_verify_export_tmp.jpg"],
            fields_to_update={"images"})
        db.commit()
        with_img = export_service.export_pdf(
            db, ExportPdfRequest(scope="manual", question_ids=[q1.id],
                                 with_answer=False), now=now)
        expect("带图题目可导出", with_img.pdf[:5] == b"%PDF-")
        blob = P.stream_bytes(with_img.pdf)
        expect("图片被写入 PDF（出现 Image XObject）",
              b"/Image" in blob or b"/XObject" in blob)
        # 对照：同一题先不注册图片时的 PDF 里不应有图片
        no_img = export_service.export_pdf(
            db, ExportPdfRequest(scope="manual", question_ids=[q3.id],
                                 with_answer=False), now=now)
        expect("无图题目的 PDF 里没有 Image XObject",
              b"/Image" not in P.stream_bytes(no_img.pdf))
    finally:
        img.unlink(missing_ok=True)

    # ---------------- Content-Disposition ----------------
    section("Content-Disposition")
    from app.routers.export import _content_disposition

    cd = _content_disposition("20260214_错题本_按文件夹.pdf")
    expect("含 RFC 5987 的 filename*", "filename*=UTF-8''" in cd, cd[:64])
    expect("含 ASCII 回退名", 'filename="cuotiben-export.pdf"' in cd)
    expect("中文已百分号编码", "%E9%94%99%E9%A2%98%E6%9C%AC" in cd)

    # ---------------- export_records 不写入 ----------------
    section("export_records 不写入（决策：暂不启用导出历史）")
    from app.models.export_records import ExportRecord

    before = db.query(ExportRecord).count()
    export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=subject.id,
                             with_answer=False), now=now)
    after = db.query(ExportRecord).count()
    expect("导出后 export_records 行数不变", after == before, f"{before} -> {after}")
    expect("表定义仍然存在（保留不删）",
           ExportRecord.__tablename__ == "export_records")
    expect("service 不再提供 record_export",
           not hasattr(export_service, "record_export"))

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
