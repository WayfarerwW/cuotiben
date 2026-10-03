"""PDF 导出业务逻辑（requirements.md 2.16 / 4.7）。

分层（AGENTS.md 3.4）：本模块是唯一入口，接收 Session，不自行开会话；
异常在这里抛出，由 router 转成 HTTP 状态码。

三个要点：
1. **中文字体**：模板用 @font-face 引 fonts/NotoSansSC-VF.ttf（SIL OFL 1.1）。
   WeasyPrint 依赖 Pango 找字，必须显式给字体文件，不能指望系统字体。
2. **图片路径**：模板里用绝对 file:// URI。WeasyPrint 以 base_url 解析
   相对路径，用 URI 可以绕开"当前工作目录"这一变量。
3. **不跨页切断**：模板用 page-break-inside: avoid 包住每道题。
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from ..models.question_images import IMAGE_KIND_ANSWER, IMAGE_KIND_STEM
from ..models.questions import MASTERY_MASTERED, MASTERY_STILL_WRONG, Question
from ..schemas import ExportScope, ExportPdfRequest
from ..weasyprint_bootstrap import chinese_font_path, ensure_native_libs
from . import image_service, question_service

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "app" / "templates"
#: 图片目录。**统一取自 image_service**，不要在这里再拼一次
#: `PROJECT_ROOT / "uploads"` —— 那样会绕过 CUOTIBEN_UPLOADS_DIR，
#: 与上传/清理用的目录不一致（自检也无法隔离），
#: 而导出正是靠这个目录把库里的相对路径解析成绝对路径的。
UPLOADS_DIR = image_service.UPLOADS_DIR

#: 文件名与页眉里用的范围中文名
SCOPE_LABELS: dict[str, str] = {
    "folder": "按文件夹",
    "tags": "按标签",
    "starred": "仅重点",
    "review_queue": "今日待复习",
    "manual": "手动勾选",
}

#: 熟练度中文名。与前端 app.js 的 MASTERY_LABEL 保持一致
#: （后端没有别处定义过，先落在本模块，避免为两个词新建常量模块）。
MASTERY_LABELS: dict[str, str] = {
    MASTERY_STILL_WRONG: "未完全掌握",
    MASTERY_MASTERED: "已掌握",
}


class ExportError(Exception):
    """导出相关错误的基类。"""


class ExportScopeError(ExportError):
    """scope 与参数不匹配，或该范围没有任何题目。"""


class ExportUnavailableError(ExportError):
    """运行环境缺少 PDF 依赖（GTK 原生库或中文字体）。"""


@dataclass
class ExportResult:
    """一次导出的产物。"""

    pdf: bytes
    filename: str
    question_count: int
    range_desc: str


# --------------------------------------------------------------------------
# 模板
# --------------------------------------------------------------------------

_env: Environment | None = None


def template_env() -> Environment:
    """Jinja2 环境（懒加载，模板目录缺失时给出可读错误）。"""
    global _env
    if _env is None:
        if not TEMPLATE_DIR.is_dir():
            raise ExportUnavailableError(f"模板目录不存在：{TEMPLATE_DIR}")
        _env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _env


# --------------------------------------------------------------------------
# 取题
# --------------------------------------------------------------------------


def resolve_questions(
    db: Session, payload: ExportPdfRequest, *, now: dt.datetime | None = None
) -> list[Question]:
    """按 scope 取出要导出的题目。

    排序策略按范围分两种，不要一律重排：
      - review_queue 保留 review_service 的展示顺序（重点优先 → 到期时间），
        导出件顺序应与"今日复习"页看到的一致；
      - 其余范围按 (sort_order, id) 固定排序 —— 导出件是静态内容，
        每次顺序都不同会让人以为内容变了。
    """
    scope = payload.scope

    if scope is ExportScope.starred:
        questions = question_service.list_questions(
            db, question_service.QuestionFilters(starred=True)
        )
    elif scope is ExportScope.folder:
        questions = question_service.list_questions(
            db, question_service.QuestionFilters(folder_id=payload.folder_id)
        )
    elif scope is ExportScope.tags:
        # 多标签按 5.5 的 AND 语义（同时含全部标签）
        questions = question_service.list_questions(
            db, question_service.QuestionFilters(tags=list(payload.tags), tag_mode="and")
        )
    elif scope is ExportScope.review_queue:
        # 已按复习队列顺序返回，直接使用
        return _review_queue_questions(db, now=now)
    elif scope is ExportScope.manual:
        # 已按用户勾选顺序返回，直接使用
        return _questions_by_ids(db, payload.question_ids)
    else:  # pragma: no cover - ExportScope 是枚举，不会有别的值
        raise ExportScopeError(f"未知的导出范围：{scope}")

    questions.sort(key=lambda q: (q.sort_order, q.id))
    return questions


def _questions_by_ids(db: Session, ids: list[int]) -> list[Question]:
    """按 id 取题，保持传入顺序（手动勾选时用户的选择顺序有意义）。

    不在这里判"存在与否"：查不到的 id 由调用方统一报错，
    否则要区分"已删除"和"从未存在"，而导出场景下都只是"选不中"。
    """
    if not ids:
        return []
    found = {
        q.id: q
        for q in question_service.list_questions(
            db, question_service.QuestionFilters(ids=list(ids))
        )
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise ExportScopeError(
            "以下题目不存在或已删除：" + "、".join(str(i) for i in missing)
        )
    return [found[i] for i in ids]


def _review_queue_questions(
    db: Session, *, now: dt.datetime | None = None
) -> list[Question]:
    """今日待复习（复用 review_service 的到期判定，避免两套逻辑漂移）。

    list_today 返回 (items, meta)，且维护了自己的展示顺序
    （重点优先 → 到期时间）。这里保留该顺序，不再重新排序 ——
    导出件的顺序应当与"今日复习"页看到的一致。

    now 必须透传：导出件的时间戳与"今日"的判定必须是同一个时刻，
    否则跨零点导出会出现"页眉写 14 号、内容却是 15 号的队列"。
    """
    from . import review_service

    items, _meta = review_service.list_today(db, now=now)
    ids = [item.question_id for item in items]
    if not ids:
        return []
    return _questions_by_ids(db, ids)


# --------------------------------------------------------------------------
# 组装模板上下文
# --------------------------------------------------------------------------


def _strip_html(html: str | None) -> str:
    """题干/答案是富文本，PDF 里按纯文本渲染。

    不直接信任存进来的 HTML：导出件会脱离应用被转发，
    这里只取文本，顺带避免把未转义内容塞进模板。
    """
    if not html:
        return ""
    import re

    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
        ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    return text.strip()


def _image_uri(file_path: str) -> str | None:
    """把库里的图片路径转成模板可用的绝对 `file://` URI。

    **必须用 image_service.absolute_path_of() 解析，不要自己拼路径。**
    库中存的是 `uploads/YYYY/MM/DD/{uuid}.jpg` 这种**日期分片**相对路径，
    前端还会传 `/uploads/...` 形式：

    - 旧实现只按文件名在 `UPLOADS_DIR / name`（uploads/ 根下）平铺查找 ——
      对分片存储**必然 miss**，因为真实文件在子目录里。
    - 于是落到兜底分支 `Path(file_path).is_file()`：按**当前工作目录**
      判断相对路径，再从 `__file__` 反推项目根去拼，得到的是**相对路径**，
      最后 `as_uri()` 直接抛 `ValueError: relative path can't be expressed
      as a file URI` —— 而 router 没拦 ValueError，整次导出变成 HTTP 500。
      用户侧的表现就是"任意地方点导出 PDF 都报错"。

    解析不出来（文件不存在）时返回 None，由模板跳过 ——
    缺一张图不该让整次导出失败。
    """
    if not file_path:
        return None
    try:
        target = image_service.absolute_path_of(file_path)
    except (OSError, ValueError):
        return None
    # 最后防线：图片是锦上添花，任何路径异常都退化成"跳过这张图"，
    # 绝不让它把整次导出弄挂。as_uri() 只接受绝对路径，否则抛 ValueError。
    try:
        if not target.is_file():
            return None
        return target.as_uri()
    except (OSError, ValueError):
        logger.warning("导出时跳过无法解析的图片：%s", file_path)
        return None


def build_context(
    questions: list[Question], payload: ExportPdfRequest, *, now: dt.datetime
) -> dict[str, Any]:
    """把 ORM 对象映射成模板上下文（显式取值，不把 ORM 泄漏进模板）。"""
    items: list[dict[str, Any]] = []
    for index, q in enumerate(questions, start=1):
        # 题干图与答案图分开传给模板：答案区在 PDF 里另起一页
        # （requirements 2.16 / 4.7），答案图片必须渲染在那一页，
        # 不能混进题干区 —— 否则"含答案"的卷子会把答案截图印在题目下面。
        ordered = sorted(q.images, key=lambda i: (i.sort_order, i.id))
        stem_images = [
            uri for uri in (_image_uri(i.file_path) for i in ordered
                            if (i.kind or IMAGE_KIND_STEM) == IMAGE_KIND_STEM)
            if uri
        ]
        answer_images = [
            uri for uri in (_image_uri(i.file_path) for i in ordered
                            if (i.kind or IMAGE_KIND_STEM) == IMAGE_KIND_ANSWER)
            if uri
        ]
        items.append({
            "no": index,
            "stem": _strip_html(q.stem) or "（未填写题干）",
            "answer": _strip_html(q.answer) or "（未填写答案）",
            "tags": [t.name for t in sorted(q.tags, key=lambda t: t.name)],
            "images": stem_images,
            "answer_images": answer_images,
            "is_starred": bool(q.is_starred),
            "mastery": q.mastery_status,
            "mastery_label": MASTERY_LABELS.get(q.mastery_status, q.mastery_status),
            "folder_name": getattr(q, "folder_name", None) or "",
        })

    scope_value = payload.scope.value if isinstance(payload.scope, ExportScope) \
        else str(payload.scope)
    return {
        "items": items,
        "total": len(items),
        "with_answer": payload.with_answer,
        "include_tags": payload.include_tags,
        "scope_label": SCOPE_LABELS.get(scope_value, scope_value),
        "generated_at": now.astimezone().strftime("%Y-%m-%d %H:%M"),
        "font_uri": chinese_font_path().as_uri(),
    }


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def render_html(context: dict[str, Any]) -> str:
    """渲染 HTML（纯 CPU，可在任意线程调用）。"""
    template = template_env().get_template("export_pdf.html")
    return template.render(**context)


def html_to_pdf(html: str) -> bytes:
    """HTML -> PDF。

    **阻塞调用**：WeasyPrint 是同步的，且大文档可能耗时数秒。
    在 FastAPI 里必须用 run_in_threadpool 包（见 routers/export.py），
    否则会卡住事件循环、连 /health 都不响应。
    """
    ensure_native_libs()
    import weasyprint  # 延迟导入：缺少 GTK 时也能给出我们的错误信息

    buf = io.BytesIO()
    weasyprint.HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(buf)
    return buf.getvalue()


def build_filename(payload: ExportPdfRequest, *, now: dt.datetime) -> str:
    """文件名含日期与范围标识（requirements.md 2.16）。

    带上数量便于在下载目录里区分同一范围的不同批次。
    """
    scope_value = payload.scope.value if isinstance(payload.scope, ExportScope) \
        else str(payload.scope)
    label = SCOPE_LABELS.get(scope_value, scope_value)
    return f"{now.astimezone():%Y%m%d}_错题本_{label}.pdf"


def range_desc(payload: ExportPdfRequest, count: int) -> str:
    """给 export_records.range_desc 与响应头用的一句话范围说明。"""
    scope_value = payload.scope.value if isinstance(payload.scope, ExportScope) \
        else str(payload.scope)
    parts = [SCOPE_LABELS.get(scope_value, scope_value)]
    if payload.scope is ExportScope.folder and payload.folder_id is not None:
        parts.append(f"folder_id={payload.folder_id}")
    if payload.scope is ExportScope.tags and payload.tags:
        parts.append("标签：" + "、".join(payload.tags))
    if payload.scope is ExportScope.manual:
        parts.append(f"手动 {len(payload.question_ids)} 题")
    parts.append(f"{count} 题")
    if payload.with_answer:
        parts.append("含答案")
    return " · ".join(parts)


def export_pdf(
    db: Session, payload: ExportPdfRequest, *, now: dt.datetime | None = None
) -> ExportResult:
    """导出主流程（同步；由 router 放到线程池里跑）。

    空结果直接报错而不是产出空白 PDF：空白 PDF 会让人以为导出成功但内容丢了。
    """
    moment = now or dt.datetime.now(dt.timezone.utc)

    questions = resolve_questions(db, payload, now=moment)
    if not questions:
        raise ExportScopeError("该范围内没有题目，未生成 PDF。")

    context = build_context(questions, payload, now=moment)
    html = render_html(context)
    pdf = html_to_pdf(html)

    # 记一条导出日志：题数、字节、被跳过的图片数。
    # 之前导出 500 只能靠翻 uvicorn 的 traceback 才定位；有这条日志，
    # "缺图但导出成功"和"图片没进 PDF"都能一眼看出来。
    attached = sum(len(q.images or []) for q in questions)
    rendered = sum(
        len(item["images"]) + len(item["answer_images"])
        for item in context["items"]
    )
    logger.info(
        "导出 PDF：范围=%s 题数=%d 字节=%d 图片=%d/%d（跳过 %d）",
        range_desc(payload, len(questions)), len(questions), len(pdf),
        rendered, attached, attached - rendered,
    )

    return ExportResult(
        pdf=pdf,
        filename=build_filename(payload, now=moment),
        question_count=len(questions),
        range_desc=range_desc(payload, len(questions)),
    )


# 导出历史（export_records）当前**不写入**：该表按 requirements.md 3.9 属可选，
# 本版本不启用"导出历史"功能，导出只生成 PDF。
# 表定义（models/export_records.py）与 ExportRecordOut 契约保留不删，
# 等启用历史功能时再在这里补 write 逻辑，并在 routers/export.py 调用。
# 之前确实实现过 record_export 并被 router 调用，按决策改为不写后已移除 ——
# 留一段没人调用的写库代码比不留更容易让人误以为"历史已经在记了"。
