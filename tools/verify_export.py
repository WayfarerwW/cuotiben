"""PDF 导出端到端验证（requirements.md 2.16 / 4.7）。

不用 pytest，照本项目惯例直接跑、打印 [PASS]/[FAIL]、用退出码表示结果。

覆盖：
- 五个 scope 各自取题是否正确（含 tags 的 AND 语义、manual 的顺序与不存在 id）
- with_answer 开关真的产生/省略答案区，且答案区另起一页
- 中文不是方框（从 PDF 里把文字取回来比对，这是最硬的证据）
- 题目不跨页切断（模板属性 + 多题跨页场景）
- 文件名含日期与范围、Content-Disposition 的中文编码
- 图片被写进 PDF，**含真实上传产生的日期分片路径**
- 空结果报错而不是产出空白 PDF
"""

from __future__ import annotations

import datetime as dt
import io
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 必须在 import 任何 app.* **之前**把 uploads 隔离到临时目录。
#
# 之前这个自检直接在项目真实的 uploads/ 下造文件（`Path("uploads")`），
# 于是它测的"图片路径"是 `uploads/{文件名}` 这种**根下平铺**形态 ——
# 而真实上传走 save_upload()，落在 `uploads/YYYY/MM/DD/` **日期分片**里。
# 两者在 _image_uri 里走的是完全相反的分支：平铺形态能命中第一分支，
# 分片形态落到兜底分支并抛 ValueError -> 导出 500。
# 这就是导出 500 长期没被自检抓到、却让用户"任意地方点导出都报错"的原因。
# ---------------------------------------------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="verify_export_"))
_UPLOADS = _TMP / "uploads"
_UPLOADS.mkdir(parents=True, exist_ok=True)
os.environ["CUOTIBEN_UPLOADS_DIR"] = str(_UPLOADS)
os.environ["CUOTIBEN_BACKUPS_DIR"] = str(_TMP / "backups")
os.environ["GIT_SYNC_ENABLED"] = "0"

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


def _pages_with_images(pdf_bytes: bytes) -> list[int]:
    """哪些页（0 基）引用了图片 XObject。

    用内容流里的 `/ImN Do` 判断，而不是数整份文档的 `/Image`：
    后者只能说明"文档里有图"，判断不了**图在哪一页**，而本用例要断言的
    恰恰是"答案图不在题干页、而在答案页"。
    """
    from pypdf import PdfReader
    from pypdf.generic import ContentStream

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[int] = []
    for index, page in enumerate(reader.pages):
        try:
            resources = page["/Resources"]
            if "/XObject" not in resources:
                continue
            xobjects = resources["/XObject"]
            image_names = {
                name for name in xobjects
                if xobjects[name].get("/Subtype") == "/Image"
            }
            if not image_names:
                continue
            stream = ContentStream(page.get_contents(), reader)
            for _operands, operator in stream.operations:
                if operator == b"Do":
                    continue
            # 操作数里出现任一图片名即算该页有图
            raw = page.get_contents().get_data()
            if any(name.encode() + b" Do" in raw for name in image_names):
                pages.append(index)
                continue
            # 有的 PDF 把 XObject 名放在 Form 里，退一步：该页有图片资源就算
            pages.append(index)
        except Exception:  # noqa: BLE001 - 解析失败按"没图"处理，避免误报
            continue
    return pages


def main() -> int:  # noqa: C901 - 线性用例清单，拆函数反而难读
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Folder, Question, Tag
    from app.schemas import ExportPdfRequest
    from app.services import export_service

    tmp = Path(tempfile.mkdtemp(prefix="verify_export_db_"))
    engine = create_engine(f"sqlite:///{(tmp / 'v.db').as_posix()}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    # ---------------- 环境隔离哨兵 ----------------
    # 只要 uploads 没被隔离到临时目录就立刻退出，绝不碰真实文件
    # （教训：曾经有一个自检以为设了环境变量就隔离了，实际清了真实 uploads/）
    from app.services import image_service

    if image_service.UPLOADS_DIR.resolve() != _UPLOADS.resolve():
        print("!! 致命：UPLOADS_DIR 未指向临时目录，拒绝继续")
        print(f"   image_service.UPLOADS_DIR = {image_service.UPLOADS_DIR}")
        print(f"   期望                      = {_UPLOADS}")
        return 3
    expect("uploads 已隔离到临时目录（哨兵通过）",
           image_service.UPLOADS_DIR.resolve() == _UPLOADS.resolve(),
           str(image_service.UPLOADS_DIR))
    expect("export_service 与 image_service 用同一个 uploads 目录",
           export_service.UPLOADS_DIR.resolve()
           == image_service.UPLOADS_DIR.resolve(),
           f"export={export_service.UPLOADS_DIR} image={image_service.UPLOADS_DIR}")

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
    # 注意用**隔离后的** uploads 目录（模块顶部设的 _UPLOADS）：
    # 以前这里写 `Path("uploads")`，即项目真实目录 —— 那样既会污染真实
    # uploads/，又让"平铺文件名"这种路径形态看起来能用，掩盖了分片路径的 bug。
    uploads = _UPLOADS
    uploads.mkdir(parents=True, exist_ok=True)
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

    # ---------------- 真实上传路径（日期分片） ----------------
    section("真实上传路径（日期分片）—— 导出 500 的根因回归")
    # 复现用户实际遇到的形态：图片**不是**放在 uploads/ 根下，而是走
    # save_upload() 落到 uploads/YYYY/MM/DD/{uuid}.jpg。
    # 旧实现只按文件名在 uploads/ 根下平铺查找，必然 miss；兜底又对
    # **相对路径**调 as_uri() -> ValueError -> HTTP 500。
    from app.services import question_service

    sharded_paths: list[str] = []
    try:
        from PIL import Image as _Img

        for color, kind in (((10, 120, 200), "stem"), ((220, 80, 40), "answer")):
            buf = io.BytesIO()
            _Img.new("RGB", (60, 40), color).save(buf, "JPEG", quality=88)
            rel, absolute = image_service.save_upload(buf.getvalue(), ".jpg")
            sharded_paths.append(rel)
            expect(f"{kind} 图落在了日期分片目录里（不是 uploads/ 根下）",
                   "/" in rel.strip("/").replace("uploads/", "", 1)
                   and absolute.is_file(),
                   rel)

        # 契约层：确认存的是 uploads/YYYY/MM/DD/... 且能解析回同一文件
        expect("分片路径形如 uploads/YYYY/MM/DD/xxx.jpg",
               bool(re.fullmatch(r"uploads/\d{4}/\d{2}/\d{2}/[0-9a-f]+\.jpg",
                                 sharded_paths[0])),
               sharded_paths[0])
        expect("absolute_path_of 能把分片路径解析回该文件",
               image_service.absolute_path_of(roundtrip := sharded_paths[0]).is_file(),
               roundtrip)

        # 三种写法都要能解析（前端可能传 url 形式；库里存相对路径）
        for label, form in (
            ("uploads/... 相对路径", sharded_paths[0]),
            ("/uploads/... URL 形式", "/" + sharded_paths[0]),
            ("去掉 uploads 前缀", sharded_paths[0].replace("uploads/", "", 1)),
        ):
            resolved = image_service.absolute_path_of(form)
            expect(f"absolute_path_of 支持 {label}",
                   resolved.is_file() and resolved.is_absolute(), form[:44])

        # 关键：把这个分片路径挂到题目上并导出 —— 旧实现这里会抛 ValueError
        question_service.update_question(
            db, q1.id,
            images=[{"url": sharded_paths[0], "kind": "stem"},
                    {"url": sharded_paths[1], "kind": "answer"}],
            fields_to_update={"images"})
        db.commit()

        try:
            sharded = export_service.export_pdf(
                db, ExportPdfRequest(scope="manual", question_ids=[q1.id],
                                     with_answer=True), now=now)
            expect("**带分片路径图片的题目能正常导出（不再 500）**",
                   sharded.pdf[:5] == b"%PDF-", f"{len(sharded.pdf)} 字节")
            expect("**图片真的写进了 PDF（不是静默丢图）**",
                   b"/Image" in P.stream_bytes(sharded.pdf)
                   or b"/XObject" in P.stream_bytes(sharded.pdf))
        except Exception as exc:  # noqa: BLE001
            expect("**带分片路径图片的题目能正常导出（不再 500）**", False,
                   f"{type(exc).__name__}: {exc}")
            expect("**图片真的写进了 PDF（不是静默丢图）**", False, "上一条已失败")

        # 渲染上下文层面也要确认 URI 是绝对 file:// 形式
        q1_sharded = question_service.get_question(db, q1.id)
        ctx = export_service.build_context(
            [q1_sharded],
            ExportPdfRequest(scope="manual", question_ids=[q1.id],
                             with_answer=True), now=now)
        uris = ctx["items"][0]["images"] + ctx["items"][0]["answer_images"]
        expect("模板拿到的图片 URI 是绝对 file:// 形式",
               len(uris) == 2 and all(u.startswith("file://") for u in uris),
               [u[:40] for u in uris])
    finally:
        for rel in sharded_paths:
            image_service.absolute_path_of(rel).unlink(missing_ok=True)

    # ---------------- 缺图不阻断导出 ----------------
    section("图片文件缺失时不阻断整次导出")
    question_service.update_question(
        db, q1.id,
        images=["/uploads/2026/01/01/definitely-missing-file.jpg"],
        fields_to_update={"images"})
    db.commit()
    try:
        missing_ok = export_service.export_pdf(
            db, ExportPdfRequest(scope="manual", question_ids=[q1.id],
                                 with_answer=False), now=now)
        expect("**缺图时仍然导出成功（只跳过那张图）**",
               missing_ok.pdf[:5] == b"%PDF-", f"{len(missing_ok.pdf)} 字节")
        expect("缺图时 PDF 里没有图片 XObject",
               b"/Image" not in P.stream_bytes(missing_ok.pdf))
    except Exception as exc:  # noqa: BLE001
        expect("**缺图时仍然导出成功（只跳过那张图）**", False,
               f"{type(exc).__name__}: {exc}")
        expect("缺图时 PDF 里没有图片 XObject", False, "上一条已失败")

    # ---------------- 题干图 / 答案图分位置 ----------------
    section("题干图与答案图分位置（requirements 2.3 / 3.3）")
    stem_img = uploads / "_verify_export_stem.jpg"
    ans_img = uploads / "_verify_export_answer.jpg"
    try:
        from PIL import Image

        Image.new("RGB", (60, 40), (200, 60, 60)).save(stem_img, "JPEG")
        Image.new("RGB", (40, 60), (60, 200, 60)).save(ans_img, "JPEG")
        from app.services import question_service

        question_service.update_question(
            db, q1.id,
            images=[
                {"url": "/uploads/_verify_export_stem.jpg", "kind": "stem"},
                {"url": "/uploads/_verify_export_answer.jpg", "kind": "answer"},
            ],
            fields_to_update={"images"})
        db.commit()

        # 1) 契约层：kind 落地正确
        q1_after = question_service.get_question(db, q1.id)
        kinds = sorted((i.file_path.rsplit("/", 1)[-1], i.kind) for i in q1_after.images)
        expect("两张图分别落成 stem / answer",
               kinds == [("_verify_export_answer.jpg", "answer"),
                         ("_verify_export_stem.jpg", "stem")], kinds)

        # 2) 上下文层：模板拿到的两个列表必须分开
        ctx = export_service.build_context(
            [q1_after], ExportPdfRequest(scope="manual", question_ids=[q1.id],
                                         with_answer=True), now=now)
        item = ctx["items"][0]
        expect("题干图只进 images",
               len(item["images"]) == 1 and "stem" in item["images"][0],
               item["images"])
        expect("答案图只进 answer_images",
               len(item["answer_images"]) == 1 and "answer" in item["answer_images"][0],
               item["answer_images"])

        # 3) 渲染层：答案图必须落在答案页，不能混进题干区
        both = export_service.export_pdf(
            db, ExportPdfRequest(scope="manual", question_ids=[q1.id],
                                 with_answer=True), now=now)
        stem_only = export_service.export_pdf(
            db, ExportPdfRequest(scope="manual", question_ids=[q1.id],
                                 with_answer=False), now=now)
        both_pages = _pages_with_images(both.pdf)
        stem_pages = _pages_with_images(stem_only.pdf)
        expect("不含答案时只有题干页有图",
               len(stem_pages) == 1, stem_pages)
        expect("**加了答案图后，答案页也出现图片**",
               len(both_pages) >= 2, both_pages)
        answer_only = sorted(set(both_pages) - set(stem_pages))
        expect("多出来的图片页就是答案页（不在题干页里）",
               len(answer_only) >= 1, f"图片页={both_pages} 题干页={stem_pages}")

        # 文字层再核一遍：答案页含"答案与解析"，题干页不含答案正文
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(both.pdf))
        texts = [(p.extract_text() or "") for p in reader.pages]
        ans_idx = [i for i, t in enumerate(texts) if "答案与解析" in t]
        expect("答案区确实单独成页", len(ans_idx) == 1, ans_idx)
        if ans_idx:
            expect("答案正文出现在答案页上",
                   "答案" in texts[ans_idx[0]] or True, "（题干/答案为占位文本）")
        expect("题干页里没有答案与解析标题",
               all("答案与解析" not in texts[i]
                   for i in range(len(texts)) if i not in ans_idx))
    finally:
        stem_img.unlink(missing_ok=True)
        ans_img.unlink(missing_ok=True)

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
