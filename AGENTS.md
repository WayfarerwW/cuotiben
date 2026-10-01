# 错题本项目 · Agent 工作规则

## 一、项目定位

纯本地单机运行的错题本 Web 应用，不上传服务器，不联网，无多用户。

- 后端：FastAPI + SQLite + SQLAlchemy
- 前端：Vue 3 + Element Plus（响应式）
- 图片：Pillow + pillow-heif
- PDF：WeasyPrint + Jinja2
- 启动：`python run.py`，浏览器访问 `http://localhost:8000`

## 二、必读文档

每次实现功能前，先阅读对应章节，确保实现符合约束：

| 文档 | 路径 | 内容 |
|---|---|---|
| 需求文档 | `docs/requirements.md` | 功能、数据模型、接口、逻辑、验收标准 |
| UI 说明书 | `docs/ui-design.md` | 色彩、字体、间距、组件、交互、响应式 |

**不要凭记忆实现，每次都要读相关章节。**

## 三、代码规范

### 3.1 后端
- 目录分层：`routers/`（路由）、`services/`（业务逻辑）、`models/`（ORM）
- 路由只做参数校验与响应，业务逻辑放 services
- 所有 datetime 用 `DateTime(timezone=True)`，存 UTC
- 外键加 `ON DELETE CASCADE`
- 删除用软删除（`deleted_at`），除非明确要求物理删除
- 图片物理文件删除放后台异步任务
- 接口返回 ISO 8601 带时区

### 3.2 前端
- Vue 3 Composition API（`setup` + `ref` / `computed`）
- 组件状态切换不用 vue-router，用组件内 ref
- API 调用统一走 `app/static/js/api.js`
- 所有 UI 严格遵循 `docs/ui-design.md` 的色彩、字体、间距、圆角、阴影
- 不要自行发挥视觉效果，设计说明书是硬约束

### 3.3 命名
- Python：snake_case
- JS：camelCase
- 数据库表名：复数小写（folders、questions、review_records）
- 组件文件名：PascalCase（NotificationPanel.vue）

## 四、关键规则（易错点，务必遵守）

### 4.1 打勾逻辑
- **不校验是否处于待复习状态**：任何题、任何时间都能打勾
- 打勾后 `interval_index` **重置为 0**
- `next_review_at = now() + INTERVALS[0]`（默认 3 天）
- **允许重复打勾，不返回 409**
- 撤销 = 软删除最近一条 review_record

### 4.2 记忆曲线
- 间隔序列默认 `[3, 7, 15, 30]` 天，存 settings
- 首次复习起点 **3 天**，不是 1 天
- 待复习判断 `next_review_at <= now()`，服务端计算
- "今日"以**本地时区 0 点**为边界

### 4.3 通知栏
- 点击列表项**在面板内直接展开复习视图**，不跳页
- 复习视图含：题干、图片、标签、答案展开、打勾、上一题/下一题
- 重点题到期：数字角标 + 红点双重提示
- 逾期 ≥14 天折叠到"积压区"

### 4.4 图片处理
- 必须显式依赖 `pillow-heif`，否则 HEIC 上传失败
- 统一输出宽度 1080px，JPEG q75
- 单图 ≤ 300KB
- 压缩失败回退原图，不阻断上传

### 4.5 草稿保护
- 四个 key：`draft_question_new`、`draft_question_{id}`、`draft_note_new`、`draft_note_{id}`
- 防抖 500ms 写入 localStorage
- 进入页面检测草稿，提示"恢复 / 丢弃"
- 提交成功后清除
- 图片不存 localStorage，只存 URL

### 4.6 标签归一化
- 入库前：trim + 小写 + 全半角转换
- 已存在则复用，不存在则新建
- 避免"极限"和"极限 "变成两个标签

### 4.7 时区
- 数据库存 UTC
- API 出入参 ISO 8601 带时区
- 前端用本地时区展示
- 时间比较一律服务端做

## 五、每步完成后同步 GitHub

每次完成代码修改后，自动执行：

```bash
git add -A
git commit -m "<type>: <描述>"   # Conventional Commits 格式
git push origin main
Commit 类型：

feat：新功能

fix：修 bug

chore：杂项（初始化、脚本）

docs：文档

refactor：重构

style：样式

示例：

feat(folders): 文件夹增删查接口

feat(review): 记忆曲线与打勾重置

feat(ui): 通知栏内嵌复习视图

fix(upload): 修复 HEIC 图片无法识别

六、风险提示（优先处理）
WeasyPrint 中文渲染：PDF 导出前先做 PoC，确认中文字体不显示为方框

HEIC 支持：pillow-heif 必须在依赖清单中

打勾逻辑：不要被默认的"409 防重复"逻辑误导，本项目的打勾是随时可打

通知栏复习视图：不要在通知面板内跳转页面，用组件内状态切换

七、不要做的事
不要引入多用户、登录、鉴权（纯本地单机）

不要引入 vue-router（用组件内状态切换）

不要引入 Celery、Redis（个人本地应用不需要）

不要用 PostgreSQL（SQLite 足够，迁移留口子即可）

不要提交 .env 到 Git

不要自行修改 UI 设计说明书的视觉参数

八、目录约定
text
cuotiben/
├── docs/                  # 需求 + UI 说明书（只读，不改）
├── AGENTS.md              # 本文件
├── app/                   # 后端 + 前端
│   ├── routers/           # 路由
│   ├── services/          # 业务逻辑
│   ├── models/            # ORM 模型
│   ├── templates/         # PDF 模板
│   └── static/            # 前端
│       ├── index.html
│       ├── css/style.css
│       └── js/
│           ├── api.js
│           └── app.js
├── data/                  # SQLite 数据库
├── uploads/               # 图片
├── backups/               # 备份
├── fonts/                 # 中文字体
├── requirements.txt
├── run.py
├── start.bat
└── start.sh
九、验收
每完成一个功能模块，对照 docs/requirements.md 第 8 节验收标准逐条检查。

text

---

## 使用方式

**第一步**：在项目根目录创建这三个文件：

```bash
mkdir -p docs
# 把上面三个代码块分别保存为：
#   docs/requirements.md
#   docs/ui-design.md
#   AGENTS.md
第二步：初始化 Git 并提交：

bash
git init
git add -A
git commit -m "docs: 添加需求文档、UI 说明书、AGENTS.md"
git remote add origin https://github.com/你的用户名/cuotiben.git
git push -u origin main
第三步：在 DSH 里开始开发。每一步的提示词只需写：

参考 docs/requirements.md 第 X 节 + docs/ui-design.md 第 Y 节。任务：<具体做什么>。

DSH 会自动去读文档，不用你每次贴全文。

第四步：如果 DSH 支持 AGENTS.md 自动加载，它会自动遵守全局规则（打勾逻辑、时区、命名等），你不需要在每步提示词里重复。