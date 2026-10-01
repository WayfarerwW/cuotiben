# 错题本 Web 应用 需求文档（纯本地单机版）

## 1. 项目概述

### 1.1 项目名称
错题本

### 1.2 项目目标
构建一个纯本地运行的错题管理 Web 应用，帮助用户按学科/知识点归档错题，通过标签快速检索，借助记忆曲线自动提醒复习，支持图片录入、重点标记、记事本、PDF 导出打印等功能。

### 1.3 使用场景
- 纯本地单机运行，不上传服务器，不联网，无多用户
- 后端本机启动，前端通过 localhost 访问
- 数据全部保存在本机文件系统
- 无鉴权，无注册登录

### 1.4 技术路线
| 层 | 技术 |
|---|---|
| 后端 | Python FastAPI |
| 数据库 | SQLite（data/cuotiben.db） |
| ORM | SQLAlchemy |
| 前端 | Vue 3 + Element Plus（响应式） |
| 图片处理 | Pillow + pillow-heif |
| PDF 导出 | WeasyPrint + Jinja2 |
| 启动 | uvicorn，浏览器访问 localhost:8000 |

### 1.5 运行环境
- OS：Windows / macOS / Linux
- 浏览器：Chromium 内核（Chrome / Edge）
- 移动端：同一局域网可通过手机浏览器访问（可选）


## 2. 功能需求

### 2.1 功能总览

| 模块 | 功能 |
|---|---|
| 文件夹管理 | 两级文件夹（学科 → 大类），树形展示 |
| 题目管理 | 大类下增删查改题目，支持图片 |
| 标签管理 | 创建标签、多标签、标签联想、归一化去重 |
| 搜索 | 按标签、按题干关键词 |
| 图片存储 | 本地存储，数据库只记路径 |
| 图片压缩 | 统一宽度 1080px，JPEG q75 |
| 草稿保护 | localStorage 自动保存，防丢失 |
| 复习通知 | 铃铛红点、今日待复习、面板内直接复习 |
| 记忆曲线 | 间隔递增，打勾后重置阶段 |
| 复习打勾 | 随时可打，打勾重置 interval_index=0 |
| 补卡机制 | 分批补卡 + 一键重置积压 |
| 重点标记 | 星标重点，优先级排序 |
| 错题正误状态 | 仍易错 / 已拿下 |
| 记事本 | 轻量笔记，支持搜索 |
| PDF 导出 | 按范围导出，含答案可选 |
| 数据说明 | 说明本机存储位置 |

### 2.2 文件夹管理

- 两级结构：一级=学科，二级=大类
- 题目只挂在二级文件夹下
- 同一父下不允许同名，应用层校验
- 删除用软删除（deleted_at）
- 前端树形展示，可展开收起
- 提供预置大学数学知识点，首次可一键导入

删除时的 force 参数（`DELETE /folders/{id}?force=false|true`）：
- 若该文件夹**或其子文件夹**下仍有未软删除的题目，默认（`force=false`）**拒绝删除**
  并返回 409，提示先处理这些题目。
- 目的：避免静默级联软删导致题目变成"挂在已删除文件夹上的孤儿"——
  界面上题目消失了，数据却还在，难以排查。
- `force=true` 表示确认要删：**只软删除文件夹本身（学科会连带其下大类），
  题目保持原状不动**（既不删除也不改所属）。

### 2.3 题目管理

字段：folder_id、stem（可空）、answer（可空）、is_starred、mastery_status、图片、标签

- 题干和答案允许为空（纯图片题目）
- 删除题目级联删除标签关联、图片关联、复习记录
- 图片物理文件删除放后台异步

### 2.4 标签管理

- 创建题目时输入标签，回车创建
- 标签归一化：trim + 小写 + 全半角转换
- 已存在则复用，不存在则新建
- 支持联想补全
- 一题可挂 1~N 个标签

### 2.5 搜索

- 按标签搜索
- 多标签 AND / OR 搜索
- 按题干关键词模糊搜索
- 支持按文件夹、标签、重点、正误状态组合筛选

### 2.6 图片存储

- 存本机 uploads/ 目录
- 目录按年月日分片，文件名 UUID
- 支持 JPG / PNG / WebP / HEIC（需 pillow-heif）
- 单文件上限 10MB
- 数据库存相对路径

目录结构：
uploads/
└── 2026/10/01/
└── {uuid}.jpg

text

### 2.7 图片压缩

- 统一输出宽度 1080px，高度等比自适应
- 统一转 JPEG，质量 75
- 不变形、不裁剪
- 单图 ≤ 300KB
- 压缩失败回退原图，不阻断上传

参数表：
| 项目 | 值 |
|---|---|
| 统一宽度 | 1080px |
| JPEG 质量 | 75 |
| 格式 | JPEG |
| 单图目标 | ≤ 300KB |

### 2.8 草稿与意外保护

- 题目编辑页、记事本编辑页输入时自动存 localStorage
- 防抖 500ms
- 草稿 key：
  - `draft_question_new`
  - `draft_question_{id}`
  - `draft_note_new`
  - `draft_note_{id}`
- 进入页面检测草稿，提示"恢复 / 丢弃"
- 提交成功后清除草稿
- 图片不存 localStorage，只存 URL
- 草稿保留 7 天

### 2.9 复习通知栏

- 右上角铃铛 + 红点/数字角标
- 点击展开"今日待复习"列表
- 列表项显示题干、文件夹、标签、星标、正误状态
- **提醒分级**：
  - 普通题到期：红点
  - 重点题到期：数字角标 + 红点
  - 重点题逾期 1~2 天：角标橙色
  - 重点题逾期 ≥3 天：角标红色 + 置顶
- **积压降权**：
  - 逾期 ≥7 天：标灰
  - 逾期 ≥14 天：折叠到"积压区"
- **点击列表项直接在面板内展开复习视图**（不跳页）：
  - 题干、图片、标签
  - 展开/收起答案
  - 打勾按钮
  - 重点标记
  - 正误切换
  - 上一题 / 下一题
  - "查看详情"入口
- "今日"以本地时区 0 点为边界
- 每 30~60s 轮询刷新

### 2.10 记忆曲线

- 题目创建时自动生成首条 review_record，`next_review_at = now() + 3天`
- 每轮复习写入新记录，记录 `interval_index`（0~3）
- 间隔序列默认 `[3, 7, 15, 30]` 天，存 settings，可配
- 间隔序列必须严格递增，由 API 层校验
- 首次复习起点默认 3 天（不用 1 天）
- 待复习判断 `next_review_at <= now()`，服务端计算
- 所有 datetime 用 `DateTime(timezone=True)`，API 用 ISO 8601 带时区

推进规则（精细模式）：
| 选择 | 动作 |
|---|---|
| 完全不会 | interval_index 归零 |
| 模糊 | 维持当前 |
| 基本掌握 | +1 |
| 已掌握 | +2 或直接到 30 天 |

### 2.11 复习打勾（核心规则）

- 题目详情页、今日列表、通知面板复习视图中，每道题提供打勾按钮
- **不校验是否处于待复习状态**：任何题、任何时间都可打勾
- 打勾 = 写入新 review_record，**interval_index 重置为 0**
- `next_review_at = now() + INTERVALS[0]`（默认 3 天）
- 打勾后若该题在今日队列中，从队列移除，红点数量刷新
- 允许重复打勾，**不返回 409**
- 撤销打勾 = 软删除最近一条 review_record

两种模式（择一）：
- 简单模式：打勾 = 重置到 0
- 精细模式：打勾时弹出掌握程度选择，按选择决定重置后阶段

### 2.12 补卡机制

- 每日补卡上限 N（默认 20，可配）
- 今日队列 = 今日到期题 + 最多 N 道逾期题
- 逾期题按"重点优先 + 逾期最久"排序
- 界面显示"今日到期 X 题，补卡 Y 题（上限 N）"
- **一键重置积压**：逾期 ≥14 天的题，interval_index 归零
  - 方式一（`spread=false`，默认）：全部重置到第一阶段，
    `next_review_at = now() + 3 天`
  - 方式二（`spread=true`）：分散重置到未来 N 天（N 取
    `settings.backfill_reset_days`，默认 14，也可由请求显式指定 `days`），
    避免积压题在同一天全部涌入今日队列
- **分散模式允许阶段与到期日不一致**：方式二下 `interval_index` 同样归零，
  但 `next_review_at` 被错开到 `[now+3天, now+3天+N]` 区间内。
  即某题可能"阶段为 0"却到期日在 6 天后 —— 这是**有意为之**：
  重置的目的是把积压摊平到未来若干天，而不是让它们立刻全部到期。
  界面展示到期日时以 `next_review_at` 为准，不要用 `interval_index` 反推。
- 重置是**修改当前生效的复习记录**（重新排期），不是新增一条打勾记录 ——
  用户并没有复习这道题。该记录会标记为补卡，计入"今日补卡数量"。
- 补卡统计：今日补卡数量、连续补卡天数

### 2.13 重点标记

- 列表、详情页、复习视图提供星标按钮
- 列表中显示醒目星标
- 支持"只看重点"筛选
- 今日列表中重点题优先排序

### 2.14 错题正误状态

- 字段 `mastery_status`：`still_wrong`（默认）/ `mastered`
- 与重点星标互补、独立
- 两个维度可组合：
  - 重点 + 仍易错：最高优先级
  - 重点 + 已拿下：重要但已掌握，降频
  - 非重点 + 仍易错：普通错题
  - 非重点 + 已拿下：可归档
- 支持按正误筛选
- 标记"已拿下"可拉长复习间隔

### 2.15 记事本

- 独立入口
- 新建、编辑、删除（软删除）
- 字段：标题、内容、created_at、updated_at
- 按更新时间倒序
- 关键词搜索
- 进阶：关联题目、Markdown 预览

### 2.16 PDF 导出

- 接口 `POST /export/pdf`
- 用 `scope` 字段区分导出范围：
  - `folder`：按文件夹
  - `tags`：按标签
  - `starred`：仅重点
  - `review_queue`：今日待复习
  - `manual`：手动勾选
- 可选含答案 / 不含答案
- 含答案时答案统一放末尾
- 排版：中文正常显示、题目不跨页切断、图片自适应、页眉页脚
- 文件名含日期与范围

请求示例：
```json
{
  "scope": "folder",
  "folder_id": 5,
  "with_answer": true,
  "include_tags": false
}
2.17 数据说明页
显示数据库路径：data/cuotiben.db

图片路径：uploads/

备份路径：backups/

说明：纯本地运行，数据保存在本机

提供手动备份、导出 JSON 入口

2.18 统计（可选）
按学科/大类统计错题数量

按标签统计分布

掌握率分析

ECharts 图表

3. 数据模型
3.1 folders
字段	类型	说明
id	INTEGER PK	主键
name	VARCHAR	文件夹名
parent_id	INTEGER FK	父文件夹，顶级为 NULL
level	INTEGER	1=学科，2=大类
sort_order	INTEGER	排序
deleted_at	DATETIME	软删除
created_at	DATETIME	创建时间
3.2 questions
字段	类型	说明
id	INTEGER PK	主键
folder_id	INTEGER FK	所属大类
stem	TEXT NULL	题干
answer	TEXT NULL	答案
is_starred	BOOLEAN	默认 false
mastery_status	VARCHAR	still_wrong / mastered
sort_order	INTEGER	排序
created_at	DATETIME	创建
updated_at	DATETIME	更新
deleted_at	DATETIME	软删除
3.3 question_images
字段	类型	说明
id	INTEGER PK	主键
question_id	INTEGER FK	题目
file_path	VARCHAR	压缩图路径
original_path	VARCHAR	原图路径（可选）
width	INTEGER	宽
height	INTEGER	高
size	INTEGER	字节
sort_order	INTEGER	排序
created_at	DATETIME	创建
3.4 tags
字段	类型	说明
id	INTEGER PK	主键
name	VARCHAR UNIQUE	归一化后标签名
created_at	DATETIME	创建
3.5 question_tags
字段	类型	说明
question_id	INTEGER FK	题目
tag_id	INTEGER FK	标签
3.6 review_records
字段	类型	说明
id	INTEGER PK	主键
question_id	INTEGER FK	题目
review_count	INTEGER	已复习次数
interval_index	INTEGER	0~3
last_review_at	DATETIME	上次复习
next_review_at	DATETIME	下次复习
mastery_level	INTEGER	0/1/2/3
is_backfill	BOOLEAN	本次是否补卡，默认 false
created_at	DATETIME	创建
deleted_at	DATETIME	撤销用

`is_backfill` 说明：打勾时若该题当前记录已逾期（`next_review_at` 早于本地今日 0 点）
即为 true；一键重置积压产生的记录同样为 true。它支撑 2.12 的
"今日补卡数量 / 连续补卡天数"统计 —— 若不落库而靠回看上一条记录反推，
记录被撤销（软删除）后就会算错。

3.7 notes
字段	类型	说明
id	INTEGER PK	主键
title	VARCHAR	标题
content	TEXT	内容
question_id	INTEGER FK	关联题目（可空）
created_at	DATETIME	创建
updated_at	DATETIME	更新
deleted_at	DATETIME	软删除
3.8 settings
字段	类型	说明
id	INTEGER PK	主键
key	VARCHAR UNIQUE	配置项
value	TEXT	值
默认配置：intervals=[3,7,15,30]、backfill_limit=20、backfill_reset_days=14

3.9 export_records（可选）
字段	类型	说明
id	INTEGER PK	主键
range_desc	VARCHAR	范围描述
question_count	INTEGER	数量
file_path	VARCHAR	文件路径
created_at	DATETIME	导出时间
3.10 关键约束
外键 ON DELETE CASCADE

图片物理删除放后台异步

tags.name 入库前归一化

无 user_id，单机单用户

4. 接口清单
4.1 文件夹

方法	路径	说明
POST	/folders	创建
GET	/folders/tree	完整树

`GET /folders/tree` 响应字段：
`id`、`name`、`parent_id`、`level`、`sort_order`、`created_at`、`deleted_at`、
`children`、`question_count`

- 顶层是学科（`level=1`、`parent_id` 为 null），`children` 是大类（`level=2`）。
- `question_count` 为只读计算字段，只统计未软删除题目；
  学科节点上汇总其下所有大类，便于折叠状态下显示总量。
- 只返回 `deleted_at` 为 null 的文件夹。

方法	路径	说明
PUT	/folders/{id}	重命名
DELETE	/folders/{id}	软删除（支持 `?force=true`，见 2.2）

4.2 题目

方法	路径	说明
POST	/questions	创建
GET	/questions	支持 folder_id / tag / keyword / starred / mastery / tag_mode

`GET /questions` 列表项响应字段：
`id`、`folder_id`、`folder_name`、`stem`、`answer`、`is_starred`、
`mastery_status`、`sort_order`、`created_at`、`updated_at`、`images`、`tags`、
`next_review_at`

方法	路径	说明
GET	/questions/{id}	详情

`GET /questions/{id}` 详情响应字段：
`id`、`folder_id`、`folder_name`、`stem`、`answer`、`is_starred`、
`mastery_status`、`sort_order`、`created_at`、`updated_at`、`deleted_at`、
`images`、`tags`、`next_review_at`、`interval_index`、`review_count`

- `images` 是对象数组（`question_images` 记录），不是 URL 字符串数组。
- `next_review_at` / `interval_index` / `review_count` 取自该题"当前生效"的
  review_record（未软删除里最新一条），由服务端推导，不直接存在 questions 表。
- `folder_name` 便于列表直接显示所属大类，避免前端再查一次文件夹。
- 列表项不返回 `deleted_at`（列表只含未删除题目，该字段无意义）；
  详情返回它以便排查。

方法	路径	说明
PUT	/questions/{id}	编辑
DELETE	/questions/{id}	软删除
POST	/questions/{id}/star	标重点
POST	/questions/{id}/unstar	取消
POST	/questions/{id}/mastery	切换正误

约定：
- 路径资源名一律用复数（questions / folders / tags / notes）。
  历史上 4.2 的详情接口写作 /question/{id}，已统一为 /questions/{id}。
- 带参路径在文档里统一写作 `{id}`，只是表示"该资源的 id"；
  代码实现里按资源命名具体化为 `{folder_id}`、`{question_id}` 等，
  生成的 OpenAPI 参数名更清晰。两者 URL 相同，不构成不一致。
- `keyword` 只匹配题干与答案，**不匹配标签**；按标签检索请用 `tag`。
  职责分离：标签检索归 `tag`，文本检索归 `keyword`。
  前端搜索框为单输入框，输入关键词时**同时请求 `?keyword=` 与 `?tag=`，
  再将两组结果合并去重**（后端不做隐式跨字段匹配）。
- `tag` 可重复传多个；`tag_mode=and`（默认）表示必须同时含全部标签，
  `tag_mode=or` 表示含任一即可。
- `mastery` 状态接口支持两种模式：
  - **不传 body**：在 `still_wrong` / `mastered` 之间翻转
  - **传 body**（`{"mastery_status": "mastered"}`）：直接设置为该值
  这样前端"切换按钮"与"明确设置"两种交互都能用同一接口。

命名审计（2026-10 全量核对第 4 节）另有两点**记录在案但暂不改动**，
因为属于动作/功能命名空间而非资源集合，改动收益低于影响面：
- `/review/today`、`/review/{id}/check` 等：`review` 是功能命名空间
- `/upload/image`、`/export/pdf`：`upload` / `export` 是动作命名空间
- `/settings`：单复数同形，无需处理

命名规范（详见 AGENTS.md 3.5）：
- **资源型接口用复数**：`/questions`、`/folders`、`/tags`、`/notes`、`/settings`
- **动作型接口用动作命名空间**：`/review/*`、`/upload/*`、`/export/*`、`/sync/*`
- 判断标准：操作实体集合 -> 复数；功能动作或视图 -> 动作命名空间。
  `/review/today` 返回的是**待复习题目列表**而非 review_records 集合，
  写成 `/reviews/today` 反而语义错误，故保持单数。

`/review/{id}` 的 `{id}` 存在语义歧义（是题目 id 还是 review_record id），
实现 4.5 时需明确为题目 id 并改名为 `{question_id}`，避免前端误传。
4.3 标签

方法	路径	说明
GET	/tags	全部

`GET /tags` 响应字段：
`id`、`name`、`created_at`、`question_count`

方法	路径	说明
GET	/tags/search?q=	联想

`GET /tags/search?q=` 响应字段：
`id`、`name`、`created_at`、`question_count`（与 `GET /tags` 同结构
`TagWithCount`），额外支持 `limit` 参数（默认 20，范围 1~100）。

返回结构：

```
GET /tags -> [
  { "id": 1, "name": "极限", "question_count": 3 },
  ...
]
```


排序规则：
- `question_count` 降序 —— 高频标签靠前
- 数量相同时按 `name` 升序，保证顺序稳定可复现

设计说明：
- `question_count` 是**只读计算字段，不存表**。查询时 JOIN `question_tags`
  聚合得出，且只统计**未软删除**的题目（已删除题目不应把计数撑高）。
- 排序让高频标签靠前，服务于两处界面需求：
  标签选择题的候选顺序、标签云的"高频标签"展示（见 ui-design 4.5 / 5.2）。
- `GET /tags/search?q=` 的查询词先做与入库相同的归一化
  （trim + 小写 + 全半角），否则输入全角「ＡＢＣ」搜不到已存的 `abc`。

4.4 图片
方法	路径	说明
POST	/upload/image	上传压缩
DELETE	/upload/image/{id}	删除
4.5 复习

方法	路径	说明
GET	/review/today	今日队列

`GET /review/today` 响应字段（数组，每项）：
`question_id`、`stem`、`answer`、`images`、`tags`、`folder_name`、
`is_starred`、`mastery_status`、`folder_id`、`interval_index`、`next_review_at`、
`overdue_days`、`is_backlog`、`is_overdue`

- `overdue_days`：逾期天数（未逾期为 0），用于标灰与角标配色。
- `is_backlog`：逾期 ≥14 天，界面折叠到"积压区"。
- `is_overdue`：`overdue_days > 0` 的便捷布尔值。
- `mastery_status`、`folder_id` 供面板内复习视图直接显示正误状态与所属文件夹。

方法	路径	说明
GET	/review/count	数量

`GET /review/count` 响应字段：`count`（与 `/review/today` 同口径）。

方法	路径	说明
POST	/review/{id}/check	打勾（随时可打）

`POST /review/{id}/check` 请求体（可选）：
`{ "mastery": 0 | 1 | 2 | 3 }`，省略则按 0 处理。
- `{id}` 是**题目 id**（不是 review_record id）—— 打勾操作的是题目，
  复习记录是打勾产生的结果。实现中参数名为 `{question_id}` 以示明确。
- 不传 body 或传空对象 `{}` 均可。该接口不校验待复习状态、允许重复打勾，
  **不返回 409**（见 2.11）。
- `mastery` 当前只写进记录的 `mastery_level` 供统计；阶段推进口径见 5.1/5.2。

方法	路径	说明
POST	/review/{id}/uncheck	撤销
POST	/review/backfill/reset	重置积压
GET	/review/backfill/stats	补卡统计

`GET /review/backfill/stats` 响应字段：
`today_backfill_count`、`consecutive_days`、`backlog_count`

- `today_backfill_count`：今日补卡数量（今天打勾且当时已逾期，或今日被重置积压）。
- `consecutive_days`：连续补卡天数；今天尚未补卡时从昨天起算。
- `backlog_count`：当前仍处于积压区（逾期 ≥14 天）的题目数。

4.6 记事本

方法	路径	说明
POST	/notes	新建
GET	/notes	列表

`GET /notes` 响应字段（按 `updated_at` 倒序）：
`id`、`title`、`content`、`question_id`、`updated_at`

列表返回 `content` 字段，超过 200 字截断加省略号。需要完整内容请调 `GET /notes/{id}`。

- `title` 与 `content` 都可空（允许先建空笔记再写）。

方法	路径	说明
GET	/notes/search?q=	搜索

`GET /notes/search?q=` 响应字段：
`id`、`title`、`content`、`question_id`、`updated_at`

关键词同时匹配 `title` 与 `content`，按 `updated_at` 倒序。
`q` 为空时返回空数组（不返回全部）；支持 `limit`（默认 100，范围 1~500）。

方法	路径	说明
GET	/notes/{id}	详情

`GET /notes/{id}` 响应字段：
`id`、`title`、`content`、`question_id`、`created_at`、`updated_at`、`deleted_at`

方法	路径	说明
PUT	/notes/{id}	编辑
DELETE	/notes/{id}	删除

- `PUT` 只更新请求体里出现的字段；传 `null` 或空串表示清空该字段。
  编辑会自动刷新 `updated_at`。
- `DELETE` 为软删除（置 `deleted_at`），记录保留在库中。

PUT 接口用 Pydantic 的 `model_fields_set` 区分"未传字段"和"传了 null"。
未传则保持原值，传 null 则清空。此规则适用于 notes、questions 等所有 PUT 接口。

4.7 导出
方法	路径	说明
POST	/export/pdf	PDF 导出
4.8 设置
方法	路径	说明
GET	/settings	获取
PUT	/settings	更新

4.9 健康检查
方法	路径	说明
GET	/health	服务状态与数据库位置

返回结构：

```
GET /health -> { "status": "ok", "db_path": "...", "db_exists": true }
```

- `db_path` 是**当前实际使用的数据库文件绝对路径**，由连接串解析得出，
  不是写死的默认路径 —— 用 `CUOTIBEN_DATABASE_URL` 覆盖连接串时也能报出真实位置。
- 用途：启动自检、确认数据落在哪个文件，以及数据说明页（2.17）显示路径。
- 该接口不涉及业务数据，无需鉴权（本项目本身也无鉴权）。

5. 关键逻辑
5.1 记忆曲线
text
INTERVALS = [3, 7, 15, 30]

推进(record, mastery):
    if mastery == 0: interval_index = 0
    elif mastery == 1: 维持
    elif mastery == 2: +1
    elif mastery == 3: +2
    record.next_review_at = now() + INTERVALS[interval_index]
5.2 打勾（随时可打，重置阶段）
text
打勾(question_id, mastery=None):
    # 不校验待复习状态
    new_interval_index = 0
    新建 review_record(
        interval_index = new_interval_index,
        next_review_at = now() + INTERVALS[0],
        mastery_level = mastery or 0
    )
5.3 撤销打勾
软删除最近一条 review_record，上一轮自动生效。

5.4 标签归一化
trim + 小写 + 全半角转换 → 查存在 → 复用或新建 → 建关联。

5.5 多标签 AND
sql
SELECT q.* FROM questions q
JOIN question_tags qt ON q.id = qt.question_id
JOIN tags t ON qt.tag_id = t.id
WHERE t.name IN (:tag_list)
GROUP BY q.id
HAVING COUNT(DISTINCT t.name) = :tag_count;
5.6 图片压缩
python
from PIL import Image
import pillow_heif, io
pillow_heif.register_heif_opener()

def compress_uniform(file_bytes, target_width=1080, quality=75):
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    w, h = img.size
    if w != target_width:
        ratio = target_width / w
        img = img.resize((target_width, int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
5.7 时区
存 UTC，DateTime(timezone=True)

API ISO 8601 带时区

"今日"以本地时区 0 点

6. 非功能需求
性能：万级题目内，列表接口 < 500ms

存储：单图 ≤ 300KB

兼容：Chromium 内核，响应式移动端

数据安全：纯本地不联网，SQLite 每日备份到 backups/

可维护：代码分层 routers / services / models

7. 目录结构
text
cuotiben/
├── docs/
│   ├── requirements.md
│   └── ui-design.md
├── app/
│   ├── main.py
│   ├── models.py
│   ├── schemas.py
│   ├── database.py
│   ├── routers/
│   │   ├── folders.py
│   │   ├── questions.py
│   │   ├── tags.py
│   │   ├── review.py
│   │   ├── notes.py
│   │   ├── upload.py
│   │   ├── export.py
│   │   └── settings.py
│   ├── services/
│   │   ├── review_service.py
│   │   ├── image_service.py
│   │   ├── tag_service.py
│   │   └── pdf_service.py
│   ├── templates/
│   │   └── pdf_template.html
│   └── static/
│       ├── index.html
│       ├── css/style.css
│       └── js/
│           ├── api.js
│           └── app.js
├── data/cuotiben.db
├── uploads/
├── backups/
├── fonts/
├── requirements.txt
├── start.bat
├── start.sh
├── run.py
└── AGENTS.md
8. 验收标准
本地启动后浏览器访问 localhost 即可用，无需登录

能创建学科和大类文件夹，树形展示

能在大类下创建题目并打多标签

标签归一化生效

多标签 AND/OR 搜索正确

上传任意尺寸图片（含 HEIC），输出宽 1080px，不变形，单图 ≤ 300KB

录入一半刷新页面，草稿自动恢复

通知栏显示待复习数量，点击在面板内直接展开复习视图

复习视图可查看题干、图片、标签，展开答案，直接打勾

复习视图支持上一题/下一题

任意题目、任意时间都能打勾，打勾后 interval_index 重置为 0

允许重复打勾，不返回 409

撤销打勾后恢复上一状态

补卡机制生效，每日上限可配，一键重置可用

重点题到期显示数字角标 + 红点

逾期 ≥14 天折叠到积压区

可标记/取消重点，可按重点筛选

可切换"仍易错 / 已拿下"，可按此筛选

记事本增删查改与搜索

可导出 PDF，中文正常，图片不变形，答案另起一页

数据说明页正确显示路径

删除文件夹/题目数据一致，无孤立记录