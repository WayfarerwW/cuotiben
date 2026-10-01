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
  - 方式一：全部重置到第一阶段
  - 方式二：分散重置到未来 N 天
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
created_at	DATETIME	创建
deleted_at	DATETIME	撤销用
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
PUT	/folders/{id}	重命名
DELETE	/folders/{id}	软删除
4.2 题目
方法	路径	说明
POST	/questions	创建
GET	/questions	支持 folder_id / tag / keyword / starred / mastery
GET	/question/{id}	详情
PUT	/questions/{id}	编辑
DELETE	/questions/{id}	软删除
POST	/questions/{id}/star	标重点
POST	/questions/{id}/unstar	取消
POST	/questions/{id}/mastery	切换正误
4.3 标签
方法	路径	说明
GET	/tags	全部
GET	/tags/search?q=	联想
4.4 图片
方法	路径	说明
POST	/upload/image	上传压缩
DELETE	/upload/image/{id}	删除
4.5 复习
方法	路径	说明
GET	/review/today	今日队列
GET	/review/count	数量
POST	/review/{id}/check	打勾（随时可打）
POST	/review/{id}/uncheck	撤销
POST	/review/backfill/reset	重置积压
GET	/review/backfill/stats	补卡统计
4.6 记事本
方法	路径	说明
POST	/notes	新建
GET	/notes	列表
GET	/notes/{id}	详情
PUT	/notes/{id}	编辑
DELETE	/notes/{id}	删除
GET	/notes/search?q=	搜索
4.7 导出
方法	路径	说明
POST	/export/pdf	PDF 导出
4.8 设置
方法	路径	说明
GET	/settings	获取
PUT	/settings	更新
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