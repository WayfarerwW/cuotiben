"""Pydantic 请求/响应模型。

对应 requirements.md 第 3 节的数据模型与第 4 节的接口清单，覆盖：
folders、questions、tags、review、notes、settings（另含图片与导出）。

约定（AGENTS.md 3.1 / 4.7）：
  - 所有 datetime 出入参都是 ISO 8601 带时区；服务端只处理 aware UTC
  - 字段用 snake_case，与数据库、前端 api.js 保持一致
  - 响应模型统一继承 OrmBase（from_attributes=True），可直接由 ORM 对象构造
  - 标记为"可选"的字段才给默认值；写入类模型不设 id / created_at
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models.settings import (
    DEFAULT_BACKFILL_LIMIT,
    DEFAULT_BACKFILL_RESET_DAYS,
    DEFAULT_INTERVALS,
)

# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------


class OrmBase(BaseModel):
    """可由 ORM 对象直接构造的响应模型基类。"""

    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    """简单操作结果。"""

    ok: bool = True
    message: str | None = None


class MasteryStatus(str, Enum):
    """错题正误状态（requirements.md 2.14）。"""

    still_wrong = "still_wrong"
    mastered = "mastered"


# --------------------------------------------------------------------------
# 4.1 文件夹
# --------------------------------------------------------------------------


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # 顶级学科不传 parent_id；大类必须传
    parent_id: int | None = None
    sort_order: int = 0


class FolderUpdate(BaseModel):
    """PUT /folders/{id} —— 重命名 / 调序。

    parent_id 不在这里修改：需求 2.2 只要求两级结构，移动节点会引入
    "跨级搬运"的额外校验，暂不支持；如需移动另开接口。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    sort_order: int | None = None


class FolderOut(OrmBase):
    id: int
    name: str
    parent_id: int | None
    level: int
    sort_order: int
    created_at: datetime
    deleted_at: datetime | None


class FolderTree(FolderOut):
    """GET /folders/tree —— 两级树，children 只在学科节点上有值。"""

    children: list[FolderTree] = Field(default_factory=list)
    # 该文件夹下的题目数量（列表页展示用）
    question_count: int = 0


# --------------------------------------------------------------------------
# 4.2 题目 / 图片
# --------------------------------------------------------------------------


class QuestionImageOut(OrmBase):
    id: int
    file_path: str
    original_path: str | None
    width: int | None
    height: int | None
    size: int | None
    sort_order: int
    created_at: datetime


class QuestionCreate(BaseModel):
    folder_id: int
    # 题干与答案都允许为空（纯图片题目，requirements.md 2.3）
    stem: str | None = None
    answer: str | None = None
    is_starred: bool = False
    mastery_status: MasteryStatus = MasteryStatus.still_wrong
    sort_order: int = 0
    # 标签名列表；服务端负责归一化 + 复用或新建（requirements.md 5.4）
    tags: list[str] = Field(default_factory=list)
    # 图片 URL 数组（上传接口尚未实现，先接受路径/URL；取不到元数据则留空）
    images: list[str] = Field(default_factory=list)


class QuestionUpdate(BaseModel):
    folder_id: int | None = None
    stem: str | None = None
    answer: str | None = None
    is_starred: bool | None = None
    mastery_status: MasteryStatus | None = None
    sort_order: int | None = None
    # 传了就整体替换该题标签；不传表示不改
    tags: list[str] | None = None
    # 同理，传了就整体替换图片列表
    images: list[str] | None = None


class TagOut(OrmBase):
    id: int
    name: str
    created_at: datetime


class QuestionOut(OrmBase):
    """题目详情（GET /question/{id}）。含图片列表与标签列表。"""

    id: int
    folder_id: int
    folder_name: str | None = None
    stem: str | None
    answer: str | None
    is_starred: bool
    mastery_status: MasteryStatus
    sort_order: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    images: list[QuestionImageOut] = Field(default_factory=list)
    tags: list[TagOut] = Field(default_factory=list)
    # 复习状态（由当前生效的 review_record 推导）
    next_review_at: datetime | None = None
    interval_index: int | None = None
    review_count: int | None = None


class QuestionListItem(OrmBase):
    """GET /questions 列表项。

    按约定必须含：id、stem、answer、is_starred、mastery_status、tags、folder_name。
    images 一并返回，列表页要显示缩略图。
    """

    id: int
    folder_id: int
    folder_name: str | None = None
    stem: str | None
    answer: str | None
    is_starred: bool
    mastery_status: MasteryStatus
    sort_order: int
    created_at: datetime
    updated_at: datetime
    images: list[QuestionImageOut] = Field(default_factory=list)
    tags: list[TagOut] = Field(default_factory=list)
    next_review_at: datetime | None = None


class MasteryUpdate(BaseModel):
    """POST /questions/{id}/mastery —— 切换正误状态。

    mastery_status 省略时表示在 still_wrong / mastered 之间翻转。
    """

    mastery_status: MasteryStatus | None = None


class QuestionFilters(BaseModel):
    """GET /questions 的筛选条件（requirements.md 2.5 / 5.5）。

    多标签默认 AND（必须同时含全部标签）；tag_mode='or' 时改为 OR。
    """

    folder_id: int | None = None
    tag: list[str] = Field(default_factory=list)
    tag_mode: Literal["and", "or"] = "and"
    keyword: str | None = None
    starred: bool | None = None
    mastery: MasteryStatus | None = None
    include_deleted: bool = False


# --------------------------------------------------------------------------
# 4.3 标签
# --------------------------------------------------------------------------


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TagWithCount(TagOut):
    """标签联想/列表附带使用次数，便于按热度排序。"""

    question_count: int = 0


# --------------------------------------------------------------------------
# 4.4 上传
# --------------------------------------------------------------------------


class ImageUploadOut(BaseModel):
    """POST /upload/image 结果（requirements.md 4.4）。

    url 是前端直接可用的访问地址（/uploads/...，不含域名）；
    file_path 是库内相对路径（uploads/...），写进 question_images.file_path。
    """

    url: str
    file_path: str
    width: int | None = None
    height: int | None = None
    size: int | None = None
    # 压缩失败回退原图时为 True（requirements.md 2.7 不阻断上传）
    fell_back_to_original: bool = False


class ImageDeleteRequest(BaseModel):
    """DELETE /upload/image —— 按路径/URL 删除，因为前端只持有 URL。"""

    file_path: str = Field(min_length=1)


# --------------------------------------------------------------------------
# 4.5 复习
# --------------------------------------------------------------------------


class ReviewRecordOut(OrmBase):
    id: int
    question_id: int
    review_count: int
    interval_index: int
    last_review_at: datetime | None
    next_review_at: datetime | None
    mastery_level: int
    created_at: datetime
    deleted_at: datetime | None


class ReviewCheckRequest(BaseModel):
    """POST /review/{question_id}/check —— 打勾（requirements.md 2.11）。

    不校验是否处于待复习状态；允许重复打勾，不返回 409。
    mastery 只在精细模式（现未启用）下影响阶段推进，见
    services/review_service.py 的 `_resolve_interval_index`。
    """

    model_config = ConfigDict(populate_by_name=True)

    # 前端字段名为 mastery（requirements.md 4.5）
    mastery: int | None = Field(default=None, ge=0, le=3)
    # 兼容旧命名
    mastery_level: int | None = Field(default=None, ge=0, le=3)

    @property
    def resolved_mastery(self) -> int | None:
        """取实际生效的掌握程度，mastery 优先。"""
        if self.mastery is not None:
            return self.mastery
        return self.mastery_level


class ReviewCheckResult(BaseModel):
    """打勾结果：返回新记录与下一次复习时间。"""

    record: ReviewRecordOut
    next_review_at: datetime | None
    interval_index: int


class ReviewItemOut(OrmBase):
    """今日队列 / 复习视图中的一道题（requirements.md 2.9 / 4.5）。

    通知面板的复习视图需要：题干、答案、图片、标签、文件夹、星标、复习状态。
    """

    question_id: int
    stem: str | None
    answer: str | None
    is_starred: bool
    mastery_status: MasteryStatus
    folder_id: int
    folder_name: str | None
    interval_index: int
    next_review_at: datetime | None
    images: list[QuestionImageOut] = Field(default_factory=list)
    tags: list[TagOut] = Field(default_factory=list)
    # 逾期天数（未逾期为 0），用于标灰 / 积压区 / 角标配色
    overdue_days: int = 0
    # 逾期 >=14 天折叠到积压区
    is_backlog: bool = False
    is_overdue: bool = False


class ReviewCountOut(BaseModel):
    """GET /review/count —— 铃铛角标用。

    只回 count：现有前端契约就只要这一个值，其他字段前端用不上；
    需要"重点题到期数、最大逾期天数"时再另开接口，避免字段膨胀后无人清理。
    """

    count: int = 0


class ReviewTodayMeta(BaseModel):
    """今日队列的构成说明，供界面显示"今日到期 X 题，补卡 Y 题（上限 N）"。"""

    due_count: int = 0
    backfill_count: int = 0
    backfill_limit: int = DEFAULT_BACKFILL_LIMIT
    overdue_total: int = 0
    total: int = 0


class ReviewTodayOut(BaseModel):
    """GET /review/today。

    按用户约定，`items` 就是返回的数组本身（见 routers/review.py 的说明）；
    本模型用于带 meta 的形态，默认不启用。
    """

    items: list[ReviewItemOut] = Field(default_factory=list)
    meta: ReviewTodayMeta = Field(default_factory=ReviewTodayMeta)


class BackfillResetRequest(BaseModel):
    """POST /review/backfill/reset —— 一键重置积压（requirements.md 2.12）。

    逾期 >= backfill_reset_days（默认 14 天）的题，interval_index 归零。

    - spread=False（默认）：全部重置到第一阶段，next_review_at = now() + 3 天
    - spread=True：分散重置到未来 N 天，避免积压题同日涌入队列；
      days 未传时取 settings.backfill_reset_days（默认 14）
    """

    spread: bool = False
    days: int | None = Field(default=None, ge=1, le=365,
                            description="分散模式的天数窗口，仅在 spread=true 时生效")


class BackfillResetResult(BaseModel):
    """重置结果。affected_count 是受影响题目数。"""

    affected_count: int = 0
    affected_question_ids: list[int] = Field(default_factory=list)
    mode: str = "all"
    spread_days: int | None = None
    first_due_at: datetime | None = None
    last_due_at: datetime | None = None


class BackfillStatsOut(BaseModel):
    """GET /review/backfill/stats —— 补卡统计（requirements.md 2.12）。"""

    today_backfill_count: int = 0
    consecutive_days: int = 0
    backlog_count: int = 0


# --------------------------------------------------------------------------
# 4.6 记事本
# --------------------------------------------------------------------------


class NoteCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    content: str | None = None
    question_id: int | None = None


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    content: str | None = None
    question_id: int | None = None


class NoteOut(OrmBase):
    id: int
    title: str | None
    content: str | None
    question_id: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class NoteListItem(OrmBase):
    """列表项：带一段内容摘要，避免列表拉全文。"""

    id: int
    title: str | None
    summary: str | None = None
    question_id: int | None
    updated_at: datetime


# --------------------------------------------------------------------------
# 4.7 导出
# --------------------------------------------------------------------------


class ExportScope(str, Enum):
    """导出范围（requirements.md 2.16）。"""

    folder = "folder"
    tags = "tags"
    starred = "starred"
    review_queue = "review_queue"
    manual = "manual"


class ExportPdfRequest(BaseModel):
    scope: ExportScope
    folder_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    question_ids: list[int] = Field(default_factory=list)
    with_answer: bool = False
    include_tags: bool = False


class ExportRecordOut(OrmBase):
    id: int
    range_desc: str | None
    question_count: int
    file_path: str | None
    created_at: datetime


# --------------------------------------------------------------------------
# 4.8 设置
# --------------------------------------------------------------------------


class SettingsOut(BaseModel):
    """GET /settings —— 已解析成正确类型（intervals 是列表而不是 JSON 字符串）。"""

    intervals: list[int] = Field(default_factory=lambda: list(DEFAULT_INTERVALS))
    backfill_limit: int = DEFAULT_BACKFILL_LIMIT
    backfill_reset_days: int = DEFAULT_BACKFILL_RESET_DAYS


class SettingsUpdate(BaseModel):
    """PUT /settings —— 只传要改的项。

    intervals 固定 4 个阶段（对应 review_records.interval_index 0~3），
    每项为正整数天数，且必须递增 —— 间隔不递增就没有"记忆曲线"的意义。
    """

    intervals: list[int] | None = Field(default=None, min_length=4, max_length=4)
    backfill_limit: int | None = Field(default=None, ge=0, le=500)
    backfill_reset_days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("intervals")
    @classmethod
    def _check_intervals(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if any(d <= 0 for d in v):
            raise ValueError("intervals 必须是正整数天数")
        if any(b <= a for a, b in pairwise(v)):
            raise ValueError("intervals 必须严格递增")
        return v
