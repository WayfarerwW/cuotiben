# 错题本项目 · Agent 工作规则

## 一、项目定位

纯本地单机运行的错题本 Web 应用，不上传服务器，不联网，无多用户。

- 后端：FastAPI + SQLite + SQLAlchemy
- 前端：Vue 3 + 原生 HTML/CSS（响应式）
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
- 业务逻辑一律放 `services/`，`router` 和 `main.py` 只做调度与挂载
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
- **不使用组件库**：所有 UI 组件用原生 HTML + CSS 自建。
  设计说明书第 2、5 节本身就是一套完整的设计系统（色彩/字号/间距/圆角/
  按钮/标签/角标），引入组件库只会带来一套需要逐项覆盖的默认样式，
  且很多组件库不支持无构建工具的 `<script>` 直接引入。
- **vendor 第三方库的规矩**：确实需要复杂组件（日期选择、图表等）而必须
  引入第三方库时，把文件放到 `app/static/js/vendor/`，并附上对应的
  LICENSE 文件；同时在 `README.md` 的「第三方依赖」一节登记
  （名称、版本、许可证、来源 URL）。纯本地离线运行，**不允许用 CDN**。
- **静态资源路径映射**（`app/main.py`）：`index.html` 里用的是相对路径，
  浏览器会把 `css/style.css` 请求到 `/css/style.css`。因此 `main.py` 有一个
  `serve_frontend_assets` 中间件，把 `/css`、`/js`、`/assets`、`/img`、
  `/fonts` 这五个顶层目录映射到 `app/static/` 下的同名目录，并带**目录穿越
  防护**（解析后必须仍在 `app/static` 之内，否则不处理）。
  不要改成把 `app/static` 挂到 `/`：那会让所有未匹配路径都落到静态查找上，
  API 的 404 语义会变混乱。
- **自建弹窗与下拉**（完整清单与验证状态见第十节）：
  - 弹窗（题目编辑 / 导出 PDF / 图片预览）：点击遮罩关闭、**Esc 关闭**、
    打开时焦点移入、Tab/Shift+Tab 在弹窗内循环、关闭后焦点归还触发元素、
    打开期间锁背景滚动。
  - 下拉（通知面板 / 用户菜单）：再次点击触发按钮关闭、**点击外部关闭**、
    **Esc 关闭**、打开时焦点进入第一项、**↑/↓ 在菜单项之间移动**（含循环与
    Home/End）、Enter/Space 触发当前项。
    菜单项用 `role="menuitem"`；通知面板里**展开的复习视图是菜单项的兄弟
    节点**，不是子节点 —— 否则里面的按钮会落进 menuitem 内部。
  - 全局键盘处理集中在 `app.js` 的 `onGlobalKeydown`：
    Esc 关浮层、Tab 锁焦点、←/→ 切题；输入框内不劫持按键。
    菜单内的 ↑/↓ 由 `onMenuKeydown` 就近处理（模板上绑 `@keydown`），
    它会 `stopPropagation`，避免和全局的 ←/→ 语义打架。
- **题目列表筛选按数据量分流**：题目数 < 500 时用客户端过滤（当前实现）；
  超过 500 后改为后端分页查询，不再把全量数据拉到前端过滤。
- **响应式断点判定用 JS 而不是纯 CSS**（`app.js` 的 `applyBreakpoint`）：
  >1200 桌面完整三栏、768~1200 平板**默认折叠 64px**、<768 移动端抽屉。
  「默认折叠但允许用户展开」这种"默认值 + 用户覆盖"的状态，纯 CSS 媒体查询
  表达不了，所以侧边栏宽度由 `.is-collapsed` 类驱动、类由 JS 按断点设置；
  用户的显式折叠操作优先于断点默认值。
- **颜色 token 按用途选，不要混用**（依据 `tools/check_contrast.py`）：
  | 用途 | 用哪个 |
  |---|---|
  | 文字 | `--c-*-text`（如 `--c-primary-text`） |
  | 白字压在上面的实色底 | `--c-primary-fill` / `--c-danger-fill` |
  | 边框、图标、装饰点、浅色底 | ui-design 2.1 原值 `--c-primary` / `--c-success` / `--c-warning` / `--c-danger` |
  渲染前跑 `python tools/check_contrast.py`（退出码非 0 即有组合未达 AA 4.5:1）。
  加新样式后若引入新的"文字 × 底色"组合，必须同步加进该脚本的清单。

### 3.3 命名
- Python：snake_case
- JS：camelCase
- 数据库表名：复数小写（folders、questions、review_records）
- 组件文件名：PascalCase（NotificationPanel.vue）

### 3.4 后端分层规范

新增一个功能模块时，固定按这四层落地，缺一不可：

| 层 | 文件 | 职责 |
|---|---|---|
| 模型 | `models/xxx.py` | 表结构与关系，不写业务规则 |
| 业务 | `services/xxx_service.py` | 业务逻辑 + 数据库操作，是唯一的写入入口 |
| 路由 | `routers/xxx.py` | 只做参数校验和响应组装 |
| 契约 | `schemas.py` | 请求/响应模型 |

- **router 只做参数校验和响应组装**：读参数、调 service、返回结果。
  不要在 router 里写查询、判断业务规则、拼装跨表数据。
- **service 承载业务逻辑和数据库操作**：查询、校验规则、事务边界都在这里。
  service 接收 `Session` 作为参数，不自行开新会话。
- **`main.py` 通过 `register_routers(app)` 统一挂载**：所有 router 在
  `app/routers/__init__.py` 汇总后一次性 include，不在业务模块里各自注册。
- 业务异常在 service 层抛出（如 `FolderNotFoundError`、
  `DuplicateFolderNameError`），由 router 转成对应 HTTP 状态码；
  service 不直接抛 `HTTPException`，保持与 Web 框架解耦。
- **状态类接口统一支持两种模式**：不传 body 为翻转，传 body 为直接设置。
  适用于 mastery 等二元状态切换接口，让前端"切换按钮"与"明确设置"
  两种交互复用同一接口，避免为同一资源开两个路由。
- **所有 PUT 接口统一用 `model_fields_set` 区分未传和传 null**。
  路由里把 `set(payload.model_fields_set)` 传给 service，
  service 按该集合决定改哪些字段。语义分三档：

  | 字段性质 | 未传 | 传 `null` |
  |---|---|---|
  | **允许为空**（如 `questions.stem` / `questions.answer`） | 保持原值 | **清空** |
  | **不允许为空**（如 `folders.name`、`questions.is_starred`） | 保持原值 | **422**（用 `field_validator` 拒绝） |
  | **配置项**（`settings.*`） | 保持原值 | **删除该配置项，回退默认值** |

  要点：
  - 不允许为空的字段必须**显式拒绝** null，而不是静默忽略 ——
    静默忽略会让前端误以为修改成功。
  - 校验"最终值"而不是"本次传入的值"：若把 `intervals` 置 null 回退默认，
    要校验回退后的默认序列，否则等于绕过校验。
  - 判断依据是"字段是否可空"，不是"传 null 安全不安全"。
  - 参考实现：`services/question_service.update_question`、
    `services/note_service.update_note`、`services/settings_service.update_settings`。
- **具体路径必须注册在参数化路径之前**：例如 `/notes/search` 要写在
  `/notes/{note_id}` 之前，否则会被参数化路径捕获（`search` 会被当成 id）。
  所有模块统一遵守；新增接口时先检查本模块内是否存在会冲突的参数化路径。

### 3.5 接口命名规范

**判断标准：如果操作的是实体集合，用复数；如果是功能动作或视图，用动作命名空间。**

| 类型 | 命名 | 示例 |
|---|---|---|
| 资源型接口 | **复数** | `/questions`、`/folders`、`/tags`、`/notes`、`/settings` |
| 动作型接口 | **动作命名空间** | `/review/*`、`/upload/*`、`/export/*`、`/sync/*` |

- 资源型：路径指向一张表/一类实体，返回的就是该集合或其成员。
  例：`GET /questions`（题目集合）、`PUT /questions/{id}`（某道题）。
- 动作型：路径指向一个功能入口或视图，返回的不一定是同名实体。
  例：`GET /review/today` 返回的是**待复习的题目列表**，不是"复习记录集合"；
  写成 `/reviews/today` 会让人误以为返回的是 review_records，语义反而错。
- 不要为了形式统一把动作型强行改成复数。两者是不同语义，不是不一致。
- 新增模块时先判断属于哪一类，再决定路径形态。
- 历史上 `GET /question/{id}`（单数资源）已统一为 `/questions/{id}`；
  `/review`、`/upload`、`/export` 经评审确认为动作命名空间，保持单数不动。

### 3.6 文档变更规则

`docs/` 下的需求文档和 UI 说明书**默认视为基线，不由 AI 主动修改**。
只有用户明确指示"修改需求文档第 X 节"或"更新 UI 说明书"时才动。
每次修改后需在 commit message 中注明改了哪一节。

- UI 说明书的视觉参数（色彩、字体、间距、圆角、阴影）是硬约束，
  即使被要求修改，也应先向用户确认这是有意调整而非笔误。
- 实现与文档冲突时，不要在实现里"顺手"改文档来掩盖分歧 ——
  应当停下来说明冲突，由用户决定改文档还是改实现。

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
├── docs/                  # 需求 + UI 说明书（默认不改，变更需明确指示）
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

## 十、TODO：可访问性收尾（部分已实现，其余待做）

需求来源：`docs/ui-design.md` 第 8 节。
`[x]` 已完成并有断言覆盖；`[ ]` 尚未实现，**不要在文档或回复里声称已完成**。

### 10.1 焦点管理
- [x] 弹窗打开时把焦点移入弹窗（`focusIntoModal`）
- [x] 弹窗内 Tab / Shift+Tab 循环，焦点不逃出弹窗（`trapFocus`）
- [x] 弹窗关闭后把焦点归还给触发它的那个元素（`rememberFocus`/`restoreFocus`）

### 10.2 弹窗键盘交互
- [x] Esc 关闭弹窗（`closeOverlays`）
- [x] 打开弹窗时给 `body` 加 `overflow: hidden` 防止背景滚动
      （`watch(modalIsOpen, updateScrollLock)`，三类弹窗任一打开即锁）

### 10.3 下拉与面板键盘交互
- [x] 点击外部关闭（`onDocumentClick`）
- [x] Esc 关闭
- [x] ↑/↓ 在菜单项之间移动焦点（含循环、Home/End；`onMenuKeydown`）
- [x] Enter / Space 触发当前项
- [x] 打开时焦点进入菜单第一项（`focusFirstMenuItem`），关闭后归还触发按钮
- 说明：通知面板的 `.notif__item-head` 是 `role=menuitem`，
  面板内**展开的复习视图**与它是**兄弟节点**而不是子节点 ——
  否则打勾/上一题/下一题这些按钮会落进 menuitem 内部，
  ↑/↓ 的移动范围也会被它们搅乱。

### 10.4 aria-live 播报
- [x] 复习打勾后播报结果
- [x] 待复习队列数量变化时播报
- [x] 笔记保存状态、草稿恢复提示的播报
- 现状：`aria-live` 共 4 处（Toast 容器、笔记保存状态、屏幕阅读器播报区、
  Toast 列表）

### 10.5 键盘可达性
- [x] `app.js` 有统一键盘处理（`onGlobalKeydown`：Esc / Tab / ←→）
- [x] 表格行内操作可 Tab 聚焦并用 Enter/Space 触发
      （都是原生 `button`，无需额外按键处理；`verify_a11y.py` 断言可达）
- [x] 标签输入联想项可 Tab 聚焦并点击（↑/↓ 选择仍未做，见下）

### 10.6 语义与标注
- [x] 图标按钮都有 `aria-label`
- [x] 动态状态补 `aria-expanded` / `aria-current` / `aria-selected`
- [x] 表格补 `<caption>`（用 `.visually-hidden` 隐藏视觉呈现，
      **不能用 `display:none`** —— 那会让读屏也读不到）

### 10.7 视觉与对比度
- [x] 对比度达标（`python tools/check_contrast.py`，21 组全通过）。
      做法（用户选定方案 B「用法规避，不动 2.1 色板」）：
      - 语义色**当填充/边框/图标**用时保持 ui-design 2.1 原值；
        当**文字**用时一律改用 `--c-*-text` 系列 token
      - 「白字 + 实色底」这种用法规避救不了的组合（白字必须落在底色上），
        增加 `--c-primary-fill` / `--c-danger-fill` 两个**填充专用** token：
        主色仅深 5%（#4A7CDB → #3E73D9，白字 4.04→4.51），
        危险色用 #C53030（白字 2.44→4.66，且与 `--c-danger-text` 同值，
        同一语义只有一个色）
      - 顺带修掉三处**本来就不在 2.1 里**的色：`--c-text-faint`(#A0AEC0)
        当正文用（2.10:1）、表格表头与快捷键提示用 `--c-text-muted`(4.02:1)、
        侧边栏状态文字 50% 白（4.03:1）
      - **新增 token 必须登记**：`--c-*-text` 用于文字、`--c-*-fill` 用于白字底色，
        不要再用裸的 `--c-success/--c-warning/--c-danger/--c-primary` 作 `color`
- [x] 状态色都不只靠颜色区分（同时有图标或文字）
- [x] 键盘焦点指示器全局可见（`:focus-visible` 6 处）

### 10.8 验证方式
- [x] `tools/verify_responsive.py`（20 项）：三个断点 + Esc/焦点移入
- [x] `tools/verify_a11y.py`（39 项，真浏览器）：锁背景滚动、Tab/Shift+Tab
      循环不逃逸、关闭后归还焦点（遮罩与 Esc 两条路径）、下拉 ↑/↓ + 循环 +
      Home/End、Enter 触发、Esc 关闭、点击外部关闭、用户菜单键盘、
      表格 `<caption>` 对读屏可见
- [x] 未做（明确记录，不算已完成）：标签输入联想项仅支持 Tab/点击，
      **没有** ↑/↓ 选择。该项不在本次要求范围内，见 10.5。



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