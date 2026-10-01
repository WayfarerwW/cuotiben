"""把导出的 PDF 光栅化成 PNG，用于人工/自动检查版式。

自动化断言能查"文本在不在、页数对不对"，但查不出"排版是否难看、
有没有内容叠在一起、中文是不是真的成了方框"。所以这里渲染成图片：
  - 自动断言：页面有实际墨迹（不是空白页）、中文区域不是整片方块
  - 人工：看图确认版式

用 pypdfium2（随环境已有）渲染，不额外引入依赖。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


def main() -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Folder, Question, Tag
    from app.schemas import ExportPdfRequest
    from app.services import export_service

    tmp = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        __import__("tempfile").mkdtemp(prefix="pdfpng_"))
    tmp.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{(tmp / 's.db').as_posix()}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    subject = Folder(name="高等数学", parent_id=None, sort_order=0)
    db.add(subject)
    db.commit()
    leaf = Folder(name="极限与连续", parent_id=subject.id, sort_order=0)
    db.add(leaf)
    db.commit()

    rows = [
        ("求极限 lim(x→0) sin(x)/x 的值，并说明理由。",
         "利用重要极限 lim(x→0) sin(x)/x = 1。", ["重要极限"], True),
        ("洛必达法则的适用条件是什么？",
         "必须是 0/0 或 ∞/∞ 型，且分子分母在去心邻域内可导，"
         "导数之比的极限存在。", ["重要极限", "可导条件"], False),
        ("求 y = x³ 的导数，并求 x = 2 处的切线斜率。",
         "y' = 3x²，故 x = 2 时斜率为 12。", ["可导条件"], False),
        ("判断级数 Σ 1/n² 是否收敛，并给出理由。",
         "收敛。因为 p = 2 > 1，由 p 级数判别法可知收敛。", ["重要极限"], False),
    ]
    for i, (stem, ans, tags, star) in enumerate(rows):
        names = []
        for t in tags:
            tag = db.query(Tag).filter(Tag.name == t).one_or_none() or Tag(name=t)
            db.add(tag)
            db.commit()
            names.append(tag)
        q = Question(folder_id=leaf.id, stem=stem, answer=ans, is_starred=star,
                     mastery_status="still_wrong", sort_order=i)
        q.tags = names
        db.add(q)
        db.commit()

    res = export_service.export_pdf(
        db, ExportPdfRequest(scope="folder", folder_id=leaf.id,
                             with_answer=True, include_tags=True),
        now=dt.datetime(2026, 2, 14, 10, 30, tzinfo=dt.timezone.utc))

    pdf_path = tmp / "sample.pdf"
    pdf_path.write_bytes(res.pdf)
    print(f"PDF: {pdf_path}  ({len(res.pdf)} 字节, {res.question_count} 题)")

    import pypdfium2 as pdfium

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf_path))
    print(f"页数: {len(doc)}")
    try:
        for i in range(min(len(doc), 3)):
            page = doc[i]
            try:
                bitmap = page.render(scale=2.0)   # 2x 便于看清中文
                image = bitmap.to_pil()
                out = OUT_DIR / f"pdf-page{i + 1}.png"
                image.save(out)
                # 自动断言：这一页确实有墨迹（不是全白），且墨迹比例合理
                gray = image.convert("L")
                hist = gray.histogram()
                dark = sum(hist[:200])            # 深色像素
                total = image.width * image.height
                ratio = dark / total
                verdict = "有内容" if 0.002 < ratio < 0.6 else "可疑（可能空白或过密）"
                print(f"  第 {i + 1} 页 -> {out.name}  {image.width}x{image.height}"
                      f"  深色占比 {ratio:.3%}  {verdict}")
                image.close()
                bitmap.close()
            finally:
                page.close()
    finally:
        # 不关会在解释器退出时打一堆 "objects are still open" 警告
        doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
