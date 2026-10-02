# 错题本项目 · Agent 工作规则

## 一、项目定位

纯本地单机运行，运行时完全离线，不上传任何第三方服务器，无多用户。
GitHub 同步仅用于开发阶段的代码与数据备份，不影响应用运行。

- 后端：FastAPI + SQLite + SQLAlchemy
- 前端：Vue 3 + 原生 HTML/CSS（响应式）
- 图片：Pillow + pillow-heif
- PDF：WeasyPrint + Jinja2
- 启动：`python run.py`，浏览器访问 `http://localhost:8000`

> 「运行时」与「开发时」必须分开说（原文只写"不联网"，与第五节的
> GitHub 同步自相矛盾）：
>
> - **运行时**：不发起任何网络请求。PDF 用本机字体、图片只存本机。
> - **开发时**：第五节的作品同步会联网，但默认关闭
>   （`GIT_SYNC_ENABLED=false`），且只同步代码与文档 ——
>   `data/`、`uploads/`、`backups/` 被 `.gitignore` 忽略，个人数据不上传。

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
- **统计图表也用自建 CSS/SVG 实现，不引入图表库**（如 ECharts）：
  这是"不使用组件库"的延伸。饼图/环形图用 `conic-gradient`，
  柱状图与进度用 CSS 宽度，折线图用内联 SVG，词云按计数映射字号。
  理由：不引入 1MB 级依赖与其 LICENSE/版本登记；canvas 图表的视觉
  容易和 ui-design 的色板、圆角、字重打架。
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
- **Vue 的 `:disabled` 必须传布尔值，不能传字符串**：
  Vue 3 运行时把 `disabled` 的**空字符串也当成真**
  （源码 `e => e && (e.disabled || "" === e.disabled)`），
  所以 `:disabled="someString"`（忙碌时 `'backup'`、空闲时 `''`）
  会让按钮**永久禁用**、点了毫无反应。本项目的做法：
  `dataBusy` 是布尔 ref，另外用 `dataBusyKind` 只负责按钮文案。
- **遮罩上的 `@mousedown` 必须带 `.self`，否则输入框点不进去**：
  写 `@mousedown.prevent`（绑在 `.modal-mask` 上、不限定目标）时，
  **弹窗内部**的 mousedown 会冒泡上来被 `preventDefault`；而"点击聚焦"
  是浏览器的默认行为，被阻止后输入框拿不到焦点 —— 现象是
  "点输入框点不进去、打字落到上次聚焦的按钮上、下拉也打不开"，
  但按钮点击仍然正常（按钮靠 `click` 触发，不依赖聚焦），
  所以看起来很像"只有文字输入坏了"。正确写法：`@mousedown.self.prevent`。
- **点击/输入类断言必须用真实事件，不能用 DOM API 造数据**：
  `el.value = 'x'` + 派发 `input`、或 `el.click()` 都会**绕过浏览器默认
  行为**（点击聚焦、按键生成字符），因此上面那条缺陷它们一律测不出来 ——
  这不是理论风险，是实际发生过的：全部浏览器自检都是绿的，而用户
  连一个字都打不进去。正确做法是用 `tools/verify_input.py`
  （CDP 真实鼠标 + 真实按键），并断言 `document.activeElement` 与输入值。
  发按键时**只发 keyDown（带 text）**，不要再补 `char`，否则字符翻倍。
- **无头浏览器验证要关掉周期任务**：`--virtual-time-budget` 会快进虚拟时间，
  app 里的 60s 通知轮询 / 5s 心跳在预算内被触发很多次，能把一次验证从
  几秒拖到几百秒。测试页用 `?nopoll=1` 让 app 跳过这两个 interval
  （`app.js` 的 `DISABLE_POLLING`），对被测行为没有影响。
- **题目列表筛选当前是客户端过滤，未做后端分页**：
  题目数 ≤ 500 时把全量题目拉到前端过滤；**超过 500 会在浏览器控制台
  打印一条警告**（`app.js` 的 `checkPaginationThreshold`，每会话只警告一次），
  阈值常量 `PAGINATION_HINT_THRESHOLD = 500`。
  仅计数监控、**不自动切换行为** —— 自动切模式会让"筛选结果不对"很难归因。

  **迁移 TODO（超过 500 题后要做）**：
  1. `GET /questions` 增加 `page` / `page_size` 参数，返回 `{items, total}`
  2. 前端只请求当前页，筛选条件（文件夹/标签/关键词/重点/掌握状态）
     一并下推给后端，不再全量拉取
  3. `filteredQuestions` 改为渲染后端返回的当前页，
     客户端只保留"已选中 id"这类纯视图状态
  4. 表格底部加分页控件；`verify_ui.py` 补分页断言
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
```

## 六、风险提示（优先处理）
WeasyPrint 中文渲染：PDF 导出前先做 PoC，确认中文字体不显示为方框

HEIC 支持：pillow-heif 必须在依赖清单中

打勾逻辑：不要被默认的"409 防重复"逻辑误导，本项目的打勾是随时可打

通知栏复习视图：不要在通知面板内跳转页面，用组件内状态切换

## 七、不要做的事
不要引入多用户、登录、鉴权（纯本地单机）

不要引入 vue-router（用组件内状态切换）

不要引入 Celery、Redis（个人本地应用不需要）

不要用 PostgreSQL（SQLite 足够，迁移留口子即可）

不要提交 .env 到 Git

不要自行修改 UI 设计说明书的视觉参数

## 八、目录约定

```text
cuotiben/
├── docs/                  # 需求 + UI 说明书（默认不改，变更需明确指示）
├── AGENTS.md              # 本文件
├── app/                   # 后端 + 前端
│   ├── routers/           # 路由（folders/questions/tags/review/notes/
│   │                      #   settings/upload/export/data/sync）
│   ├── services/          # 业务逻辑（含 data_service / sync_service
│   │                      #   / image_service）
│   ├── models/            # ORM 模型
│   ├── templates/         # PDF 模板
│   ├── database.py        # 引擎/会话/init_db + 轻量迁移（LIGHT_MIGRATIONS）
│   ├── schemas.py         # 请求/响应契约
│   ├── main.py            # 应用装配：register_routers + 中间件 + lifespan
│   └── static/            # 前端
│       ├── index.html
│       ├── css/style.css
│       └── js/
│           ├── api.js
│           ├── app.js
│           └── vendor/    # 第三方库（须附 LICENSE 并在 README 登记）
├── tools/                 # 环境自检与验证脚本（verify_*.py / check_contrast.py 等）
├── data/                  # SQLite 数据库（不入库）
├── uploads/               # 图片（不入库）
├── backups/               # 备份（不入库）
├── fonts/                 # 中文字体
├── requirements.txt
├── .env.example           # 配置模板（.env 不入库）
├── run.py                 # 启动入口（--dev 才开热重载）
├── backup.py              # 每日备份：backups/cuotiben_{date}.db，保留 30 天
├── start.bat
└── start.sh
```

## 九、验收
每完成一个功能模块，对照 docs/requirements.md 第 8 节验收标准逐条检查。

## 十、可访问性收尾（已全部完成）

需求来源：`docs/ui-design.md` 第 8 节。
`[x]` = 已实现且**有断言覆盖**；`[ ]` = 尚未实现，**不要在文档或回复里
声称已完成**。当前第十节全部为 `[x]`。

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
- [x] 标签联想支持键盘：↑/↓ 移动高亮（含边界循环）、Enter 选中填入输入框、
      Esc 关闭下拉但保留输入、Tab 关闭下拉。
      输入框用 combobox + `aria-activedescendant` 指到高亮项，
      高亮时焦点仍留在输入框里（可以继续打字）。
      **两种 Enter 是刻意的**：有键盘高亮时只把该标签**填入输入框**等用户确认；
      无高亮时沿用原行为**直接新建**。

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
- [x] `tools/verify_a11y.py`（97 项，真浏览器）：锁背景滚动、Tab/Shift+Tab
      循环不逃逸、关闭后归还焦点（遮罩与 Esc 两条路径）、下拉 ↑/↓ + 循环 +
      Home/End、Enter 触发、Esc 关闭、点击外部关闭、用户菜单键盘、
      表格 `<caption>` 对读屏可见、打勾与笔记草稿的 aria-live 实际播报文本、
      标签联想键盘（↑/↓ 移动 + 边界循环、Enter 选中填入、Esc/Tab 关闭且保留
      输入、无高亮时 Enter 仍新建）、数据说明页（路径与说明文案、
      两个按钮**空闲态不带 disabled**、真点备份出结果、导出 JSON 不报错）、
      超 500 题时的分页迁移警告
      （第二遍运行，塞够数据后重新加载页面捕获 console.warn）

**第十节全部完成。** 唯一保留的说明：无头 + 虚拟时间下 Vue 的 DOM 回写
不完全可靠（响应式值已清空但 input.value 没跟着回写），因此个别断言改为
查应用自己写下的状态（如 localStorage 草稿）或真实 DOM 属性，
而不是依赖"某个元素有没有重新渲染出来" —— 否则测的是环境而不是功能。
