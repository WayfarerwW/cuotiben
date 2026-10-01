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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    `name` 为必填项，不允许传 null：传 null 由校验器直接返回 422，
    而不是静默忽略（否则前端会以为改名成功了）。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def _reject_null_name(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("name 为必填，不允许传 null")
        return v


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
    """PUT /questions/{id} 编辑（requirements.md 4.2）。

    字段分两类（配合 `model_fields_set` 使用）：

    - **允许为空的字段**：`stem`、`answer`。
      传 `null` 表示清空（需求 2.3 允许纯图片题目，题干答案都为空）。
    - **不允许为空的字段**：`folder_id`、`is_starred`、`mastery_status`、
      `sort_order`、`tags`、`images`。
      它们在库里都非空，所以传 `null` 直接 422，而不是静默当作"清空"。

    "未传"与"传 null"的区别由 `model_fields_set` 表达，见
    app/services/question_service.update_question。
    """

    folder_id: int | None = Field(default=None, ge=1)
    stem: str | None = None
    answer: str | None = None
    is_starred: bool | None = None
    mastery_status: MasteryStatus | None = None
    sort_order: int | None = None
    tags: list[str] | None = None
    images: list[str] | None = None

    @field_validator("folder_id", "is_starred", "mastery_status", "sort_order",
                     "tags", "images")
    @classmethod
    def _reject_null_for_non_nullable(cls, v, info):
        if v is None:
            raise ValueError(f"{info.field_name} 不允许传 null")
        return v


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

    id 是 question_images 主键 —— 前端录题拿到它之后，
    才能用 DELETE /upload/image/{id} 删除（见 2.6 图片存储）。
    url 是前端直接可用的访问地址（/uploads/...，不含域名）；
    file_path 是库内相对路径（uploads/...），写进 question_images.file_path。
    """

    id: int
    url: str
    file_path: str
    width: int | None = None
    height: int | None = None
    size: int | None = None
    # 压缩失败回退原图时为 True（requirements.md 2.7 不阻断上传）
    fell_back_to_original: bool = False


class ImageDeleteResult(BaseModel):
    """DELETE /upload/image/{id} 结果。"""

    image_id: int
    file_path: str
    # 该图片被多少个其它题目/关联引用并已解绑
    unbound_questions: int = 0
    # 物理文件是否真的删掉了（文件可能已被手工清理）
    file_deleted: bool = False


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
    """列表项。

    按前端契约返回 `content` 本身。极长的笔记会把列表响应撑大，
    因此超过 MAX_LIST_CONTENT 时截断（见 note_service），
    详情接口始终返回完整内容。
    """

    id: int
    title: str | None
    content: str | None
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
    """POST /export/pdf 请求体（requirements.md 2.16）。

    各 scope 需要的额外字段：
      - folder       用 folder_id（不传 = 全部题目）
      - tags         用 tags（多标签，按 5.5 的 AND 语义）
      - starred / review_queue  无需额外字段
      - manual       用 question_ids
    """

    scope: ExportScope
    folder_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    question_ids: list[int] = Field(default_factory=list)
    with_answer: bool = False
    include_tags: bool = False

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        # 标签归一化（trim/小写/全半角）由 tag_service 负责（6.6），
        # 这里只挡掉全空白的输入。
        return [t for t in v if t and t.strip()]

    @model_validator(mode="after")
    def _check_scope_requirements(self) -> "ExportPdfRequest":
        """scope 必需字段缺失时直接 422，不要静默导出一份空白 PDF。

        静默返回空产物会让用户以为"导出成功但没内容"，比报错更难排查。
        """
        if self.scope is ExportScope.manual and not self.question_ids:
            raise ValueError("scope=manual 时必须提供 question_ids")
        if self.scope is ExportScope.tags and not self.tags:
            raise ValueError("scope=tags 时必须提供 tags")
        return self


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
    """PUT /settings —— 批量更新（requirements.md 4.8）。

    只传要改的项。`model_fields_set` 区分"未传"与"传 null"：

    - **未传**：保持原值不动
    - **传 null**：删除该配置项，回退到默认值
      （intervals -> [3,7,15,30]、backfill_limit -> 20、
      backfill_reset_days -> 14）

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


# --------------------------------------------------------------------------
# 数据说明页（requirements.md 2.17）
# --------------------------------------------------------------------------


class DataPathsOut(BaseModel):
    """GET /data/paths —— 页面要展示的路径与实际存在性。

    路径由服务端用**当前生效**的配置解析（CUOTIBEN_DATABASE_URL 可覆盖），
    不是把 2.17 里写的字面值硬编码回去 —— 否则用户会照着页面去备份一个空文件。
    """

    db_path: str
    db_exists: bool
    uploads_path: str
    uploads_exists: bool
    uploads_file_count: int
    backups_path: str
    backups_exists: bool


class BackupResultOut(BaseModel):
    """POST /data/backup 的响应。"""

    backup_dir: str
    db_bytes: int
    image_count: int
    created_at: datetime


# --------------------------------------------------------------------------
# GitHub 自动同步（AGENTS.md 五）
# --------------------------------------------------------------------------


class SyncStateOut(BaseModel):
    """同步运行时状态。"""

    running: bool = False
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str = ""
    last_action: str = ""
    last_commit: str = ""
    last_push: str = ""
    sync_count: int = 0
    failure_count: int = 0
    thread_alive: bool = False


class WatchedDirOut(BaseModel):
    """一个被监控的目录。"""

    path: str
    exists: bool
    fingerprint: str
    changed_since_last_sync: bool


class SyncStatusOut(BaseModel):
    """GET /sync/status。

    	oken_present 只报"有没有配置 token"，**绝不回显内容**。
    watched_dirs_gitignored 如实说明这三个目录不会入库 ——
    否则用户会误以为数据也同步到 GitHub 了。
    """

    enabled: bool
    configured: bool
    ready: bool
    repo_url: str
    token_present: bool
    interval_minutes: int
    branch: str
    thread_alive: bool
    dirty: bool
    watched: dict[str, WatchedDirOut]
    watched_dirs_gitignored: dict[str, bool]
    note: str
    state: SyncStateOut


class SyncRunOut(BaseModel):
    """POST /sync/now 的结果。"""

    status: str
    action: str = ""
    commit: str = ""
    pushed: bool = False
    message: str = ""
    reason: str = ""
