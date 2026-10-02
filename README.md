# 错题本（纯本地单机版）

纯本地运行的错题管理 Web 应用：按学科/知识点归档错题，标签检索，记忆曲线自动提醒复习，支持图片录入、重点标记、记事本、PDF 导出打印。

- **不联网、不上传服务器、无多用户、无登录鉴权**
- 数据全部保存在本机文件系统

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python FastAPI |
| 数据库 | SQLite（`data/cuotiben.db`） |
| ORM | SQLAlchemy |
| 前端 | Vue 3 + 原生 HTML/CSS（响应式） |
| 图片处理 | Pillow + pillow-heif |
| PDF 导出 | WeasyPrint + Jinja2 |
| 启动 | uvicorn，浏览器访问 `http://localhost:8000` |

## 第三方依赖

本项目要求**纯本地离线运行，不使用 CDN**。确需引入的第三方库一律 vendor 到
`app/static/js/vendor/`（字体放 `fonts/`），随仓库一起分发，并在此登记。

新增 vendor 库时按同样格式补一行，同时把 LICENSE 文件一并放进同目录。

| 名称 | 版本 | 许可证 | 来源 | 本地路径 |
|---|---|---|---|---|
| Vue（`vue.global.prod.js`） | 3.5.43 | MIT | https://unpkg.com/vue@3.5.43/dist/vue.global.prod.js | `app/static/js/vendor/vue.global.prod.js` |
| 思源黑体 / Noto Sans SC（`NotoSansSC-VF.ttf`） | 2.04 | SIL OFL 1.1 | Adobe 官方发布（Noto Sans SC 即思源黑体） | `fonts/NotoSansSC-VF.ttf` |

许可证文件：

- Vue：`app/static/js/vendor/vue.LICENSE.txt`
- 思源黑体：`fonts/NotoSansSC-OFL.txt`（字体版权归 Adobe，
  文件名与 name 表均记作 "Noto Sans SC"，即思源黑体的 Google 发行名）

> 说明：UI 组件全部用原生 HTML + CSS 自建，未使用任何组件库
> （见 AGENTS.md 3.2）。Vue 只作为响应式运行时被引入。

## 目录结构

```
cuotiben/
├── docs/                  # 需求文档 + UI 说明书（只读，不改）
├── AGENTS.md              # Agent 工作规则
├── app/                   # 后端 + 前端
│   ├── main.py            # 应用入口：API 路由 → 静态挂载 → 根路径
│   ├── routers/           # 路由（只做参数校验与响应）
│   ├── services/          # 业务逻辑
│   ├── models/            # ORM 模型
│   ├── templates/         # PDF 模板
│   └── static/            # 前端资源
│       ├── index.html     # Vue 模板（挂载点）
│       ├── css/style.css  # 设计令牌 + 全部样式
│       └── js/
│           ├── api.js     # 接口封装
│           ├── app.js     # Vue 主逻辑
│           └── vendor/    # vendored 第三方库（见「第三方依赖」）
├── data/                  # SQLite 数据库（data/*.db 已忽略）
├── uploads/               # 图片（按年月日分片，文件名 UUID）
├── backups/               # 数据库每日备份
├── fonts/                 # 中文字体（PDF 导出用，Noto Sans SC）
├── tools/                 # 环境自检与验证脚本
├── poc_out/               # 自检产物（已在 .gitignore 忽略）
├── requirements.txt
├── run.py                 # 启动入口（uvicorn 127.0.0.1:8000, reload）
├── start.bat              # Windows 启动脚本
├── start.sh               # macOS / Linux 启动脚本
└── README.md
```

## 安装与运行

### 1. Python 依赖

```bash
# 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

> 注意：`C:\msys64\ucrt64\bin` 里也有一个 `python.exe`。不要把该目录放在 PATH 前面，
> 否则 `python` 会解析到 MSYS2 的解释器，报 `No module named 'weasyprint'`。
> 启动时请用虚拟环境里的解释器（或绝对路径）。

### 2. WeasyPrint 原生库（PDF 导出必需，Windows）

`pip install weasyprint` **不含** Pango / GObject / Cairo 等原生库，Windows 上必须另装，
否则 `import weasyprint` 直接失败：

```
OSError: cannot load library 'libgobject-2.0-0'
```

用 MSYS2 提供（一次性安装）：

```powershell
winget install --id MSYS2.MSYS2 --silent --accept-package-agreements --accept-source-agreements
```

```bash
# 国内建议先把 /etc/pacman.d/mirrorlist.mingw 首行换成清华源，否则会下载超时：
#   Server = https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/$repo/
pacman -S --noconfirm --needed \
  mingw-w64-ucrt-x86_64-pango mingw-w64-ucrt-x86_64-gdk-pixbuf2 \
  mingw-w64-ucrt-x86_64-libffi mingw-w64-ucrt-x86_64-harfbuzz \
  mingw-w64-ucrt-x86_64-fontconfig mingw-w64-ucrt-x86_64-freetype \
  mingw-w64-ucrt-x86_64-glib2
```

装入 `C:\msys64\ucrt64\bin`。若装到别处，用环境变量 `CUOTIBEN_GTK_BIN`
指向含 `libgobject-2.0-0.dll` 的目录，或设置 `MSYS2_ROOT`。

详见 [docs/poc-weasyprint.md](docs/poc-weasyprint.md)。**装不上不影响录题、复习等核心功能，
只影响 PDF 导出。**

### 3. 启动

```bash
python run.py
```

也可以用启动脚本（内容就是 `python run.py`，只是省掉敲命令）：

```powershell
# Windows：双击，或
start.bat
```

```bash
# macOS / Linux（首次需加执行权限）
chmod +x start.sh
./start.sh
```

启动后浏览器访问 <http://localhost:8000>。

`run.py` 的可选参数：

| 参数 | 说明 |
|---|---|
| `--port 9000` | 换端口（默认 8000） |
| `--host 0.0.0.0` | 允许局域网访问（默认 `127.0.0.1` 仅本机） |
| `--dev` | 开发模式：改代码自动重启 |

> 默认**不开** uvicorn 的热重载。它会让 uvicorn 监视整个项目目录，
> 任何文件变动都重启进程 —— 包括 GitHub 自动同步自己产生的改动，
> 那会把后台同步线程反复杀掉重启。改代码自动重启只在开发时有用，
> 所以挪到了 `--dev`。

> 想确认"启动后确实能打开、没有 404、Vue 正常挂载"，
> 可运行 `python tools/verify_launch.py`（会用临时库真实启动一次并逐项检查）。

### 4. 每日备份（建议配置）

`backup.py` 把 `data/cuotiben.db` 复制成 `backups/cuotiben_{YYYY-MM-DD}.db`，
并只保留最近 30 天：

```bash
python backup.py              # 备份今天 + 清理超过 30 天的
python backup.py --keep 7     # 只保留最近 7 天
python backup.py --list       # 列出已有备份和占用
python backup.py --force      # 今天已备份过也重做
python backup.py --dry-run    # 只显示会做什么
```

排期（三者选一）：

```bash
# Linux / macOS：crontab -e，每天 3 点
0 3 * * *  cd /path/to/cuotiben && /usr/bin/python3 backup.py >> backups/backup.log 2>&1
```

```powershell
# Windows：计划任务里每天运行
schtasks /create /tn "cuotiben-backup" /sc daily /st 03:00 ^
  /tr "python D:\path\to\cuotiben\backup.py"
```

```bash
# 或者让应用自己兜底：.env 里设 CUOTIBEN_AUTO_BACKUP=1
# 应用每次启动时补做当天这一次（同一天不会重复备）
```

> 两种情况都建议：**排期**保证每天都备，**启动兜底**保证不排期也不会
> 一直没备份。备份用的是 SQLite 自己的 `backup()` API 做一致性快照，
> 不直接拷 `.db` 文件（有未提交事务或开着 WAL 时，直接拷可能拿到
> 不一致的快照）。

### 环境自检

```bash
python tools/verify_launch.py          # 启动可访问性：run.py 起服务、静态资源无 404、Vue 挂载
python tools/verify_input.py           # 真实输入：点击聚焦、真实按键打字、下拉选择、标签回车、撤销打勾
python tools/verify_detail.py          # 题目详情：答案默认遮住（真不渲染）、揭开/收起、只读、深链接
python tools/verify_orphans.py         # 孤儿图片：判定规则、清理、防目录穿越、uploads 隔离
python tools/verify_responsive.py      # 响应式：三个断点 + Esc/焦点移入（真浏览器）
python tools/verify_a11y.py            # 可访问性：锁滚动/Tab 循环/焦点归还/下拉与标签键盘/数据页
python tools/check_contrast.py         # 对比度：文字×底色组合是否达 WCAG AA 4.5:1
python tools/poc_weasyprint_cjk.py     # PDF 中文渲染 PoC（字体嵌入 + 中文可提取）
python tools/verify_export.py          # PDF 导出：五个 scope、答案另起一页、不跨页
python tools/verify_export_http.py     # PDF 导出 HTTP 层：响应头/422/线程池不阻塞事件循环
python tools/render_pdf_preview.py     # 把导出的 PDF 渲染成 PNG，便于肉眼确认版式
python tools/verify_db_schemas.py      # 数据库连接、建表、settings 默认值、Pydantic schema
python tools/verify_models.py          # ORM 表结构、外键级联、datetime 往返
python tools/verify_softdelete.py      # 软删除与部分唯一索引
python tools/verify_folders_service.py # folders 业务逻辑（两级结构、同名、级联软删除）
python tools/verify_questions_service.py # questions 业务逻辑（标签归一化、筛选、首条复习记录）
python tools/verify_image_service.py   # 图片压缩（1080px/JPEG q75/≤300KB）、上传接口、静态挂载
python tools/verify_review_service.py  # 复习：首条记录、打勾重置、撤销、今日边界、补卡与重置积压
python tools/verify_tags.py            # 标签：归一化、复用、联想、多标签 AND/OR、计数
python tools/verify_notes.py           # 记事本：增删查改、软删除、标题/内容模糊搜索
python tools/verify_settings.py        # 设置：默认值初始化、批量更新、校验、容错
python tools/verify_data_service.py    # 数据说明页：路径解析、手动备份、导出 JSON
python tools/verify_backup.py          # 每日备份脚本：文件名/保留策略/一致性快照/启动兜底
python tools/verify_sync.py            # GitHub 自动同步：真推到临时 bare 仓库、token 不落盘
python tools/verify_design_tokens.py   # 前端：style.css 与 UI 说明书的色彩/字体/间距/圆角/阴影/断点一致性
python tools/verify_font.py            # 中文 PDF 渲染与字体嵌入
python tools/verify_heic.py            # HEIC 读写与图片压缩管线
python tools/audit_routes.py           # 接口审计：命名一致性 + 文档与实现的字段级差异
```

`tools/curl_*.ps1` 会真实启动 uvicorn 打一遍接口（用独立临时库与空闲端口，
不碰 `data/cuotiben.db`）：

```powershell
powershell -ExecutionPolicy Bypass -File tools/curl_folders.ps1
powershell -ExecutionPolicy Bypass -File tools/curl_questions.ps1
powershell -ExecutionPolicy Bypass -File tools/curl_review.ps1
powershell -ExecutionPolicy Bypass -File tools/curl_api_js.ps1   # 前端 api.js 对真后端的契约自检
```

> Windows PowerShell 5.1 注意：这些脚本必须存为 **UTF-8 with BOM**，否则中文会被按 GBK 解读而报语法错误。
> `curl_api_js.ps1` 需要 node 在 PATH 中（断言逻辑在 `tools/verify_api_js.js`）。

前端（Vue 应用）的验证需要先在**同一个 origin** 上提供静态文件与 API，
再在无头浏览器里操作页面。两步都要 node 与 Edge：

```powershell
# 1) 起测试服务器（静态前端 + 后端 API + 测试页，同一 origin）
python tools/_serve_for_ui_test.py 8941 <harness.html 路径> <临时库路径>
# 2) 跑断言（会自行造数据、回拨到期时间，然后驱动浏览器）
python tools/verify_ui.py http://127.0.0.1:8941/ <临时库路径>
```

> `tools/_serve_for_ui_test.py` 以下划线开头，是**测试脚手架**，不是产品代码：
> 它只是为了让"静态前端 + API + 测试页"同源，便于浏览器驱动。
> 真实运行时前端由 `app/main.py` 直接提供（已挂载：`serve_frontend_assets`
> 中间件 + 根路径返回 `index.html`），不需要这个脚手架。
> 更省事的做法是直接跑 `python tools/verify_a11y.py <port>`，
> 它自己会起脚手架、造数据、驱动浏览器。
>
> `verify_input.py` 走 Chrome DevTools Protocol 发**真实鼠标与键盘事件**，
> 需要额外装一个自检专用依赖：`pip install websocket-client`
> （不是运行依赖，没装在 `requirements.txt` 里）。它存在的理由见
> [AGENTS.md](AGENTS.md) 3.2「点击/输入类断言必须用真实事件」。

## GitHub 自动同步（可选，默认关闭）

把仓库里的改动定期 `add` + `commit` + `push` 到 GitHub。
这是本项目**唯一会联网**的部分，不开启就完全离线运行。

> ⚠️ **只同步代码与文档。** `data/`、`uploads/`、`backups/`
> 在 `.gitignore` 里是刻意忽略的（个人数据不入库），同步服务**不会**
> 用 `-f` 绕开它们。这三个目录仅用于触发变更检测。
> 意思是：**你的题库和图片不会被同步到 GitHub。**

配置（复制 `.env.example` 为 `.env` 再改）：

```bash
GIT_SYNC_ENABLED=1
GIT_SYNC_REPO_URL=https://github.com/<你的用户名>/<仓库名>.git
GIT_SYNC_TOKEN=ghp_xxxxxxxx        # 需要 repo 写权限的 PAT
GIT_SYNC_INTERVAL_MINUTES=10       # 每多少分钟检查一次
```

启动服务后即生效（首次同步在启动时自动执行）。相关接口：

| 接口 | 说明 |
|---|---|
| `GET /sync/status` | 配置与运行状态；`token_present` 只报有无，不回显内容 |
| `POST /sync/now` | 立即同步一次；未启用返回 409，出错返回 502 |

**Token 不会落盘。** 推送时通过 `GIT_ASKPASS` 临时注入，只存在于那一次
git 子进程的环境变量里，**不写入 `.git/config`**（把 token 拼进 remote URL
会以明文永久留在磁盘上）。启动时还会检查 remote URL，若已含凭据则自动移除。
自检 `python tools/verify_sync.py` 会直接断言 `.git/config` 里搜不到 token。

`.env` 已被 `.gitignore` 忽略（含 `.env.*`，但保留 `.env.example` 入库）。

## 文档

实现任何功能前先阅读对应章节，不要凭记忆实现：

| 文档 | 内容 |
|---|---|
| [docs/requirements.md](docs/requirements.md) | 功能、数据模型、接口、关键逻辑、验收标准 |
| [docs/ui-design.md](docs/ui-design.md) | 色彩、字体、间距、组件、交互、响应式 |
| [docs/poc-weasyprint.md](docs/poc-weasyprint.md) | PoC 结论：WeasyPrint 中文渲染、字体选型、HEIC 读写 |
| [AGENTS.md](AGENTS.md) | 代码规范、关键规则（打勾逻辑、时区、命名等） |

## 数据存储位置

所有数据都在**本机**，不上传任何服务器（唯一的例外是可选、默认关闭的
GitHub 自动同步，且它**只同步代码文档，不含你的题库与图片**）。

| 内容 | 路径 | 是否入库 |
|---|---|---|
| 数据库 | `data/cuotiben.db` | 否（`.gitignore` 忽略） |
| 图片 | `uploads/YYYY/MM/DD/{uuid}.jpg` | 否 |
| 每日备份 | `backups/cuotiben_{YYYY-MM-DD}.db`（保留 30 天） | 否 |
| 手动备份 | `backups/{YYYYMMDD-HHMMSS}/`（数据库 + uploads 副本） | 否 |
| 中文字体 | `fonts/NotoSansSC-VF.ttf`（Noto Sans SC，SIL OFL 1.1） | 是 |
| 环境变量 | `.env`（由 `.env.example` 复制而来） | 否（**含密钥，绝不入库**） |
| 环境自检脚本 | `tools/` | 是 |

数据库、图片、备份三个目录被 `.gitignore` **刻意**忽略，只有 `.gitkeep`
占位文件入库，所以 clone 下来目录结构是完整的。

> 关于"每日备份"与"手动备份"的区别：前者是**单文件快照**，只含数据库，
> 靠文件名日期保留最近 30 份，适合每天自动跑；后者是**一整个目录**，
> 含数据库 + `uploads/` 图片，在数据说明页点「手动备份」触发，适合做
> 迁移或重要改动前的存档。两者互不干扰，清理前者不会碰到后者。

**迁移到另一台机器**：把 `data/`、`uploads/`、`backups/`（以及你的
`.env`）一起复制过去即可。

## 使用方式（在本仓库上继续开发时）

需求文档与 UI 说明书是**基线**：默认不改，实现与文档冲突时先说明冲突，
由人决定改文档还是改实现（见 [AGENTS.md](AGENTS.md) 3.6）。

每一步的提示词只需写清「参考哪一节 + 要做什么」，不必把文档全文贴进来：

```
参考 docs/requirements.md 第 X 节 + docs/ui-design.md 第 Y 节。任务：<具体做什么>。
```

若工具支持自动加载 [AGENTS.md](AGENTS.md)，其中的全局规则
（打勾逻辑、时区、命名、分层等）会自动生效，无需在每步提示词里重复。

> 本仓库最初是由三个文件生成出来的（`docs/requirements.md`、
> `docs/ui-design.md`、`AGENTS.md`），那份"从零创建仓库"的引导步骤
> 已经完成、也已从 AGENTS.md 移到本文件；AGENTS.md 现在只保留运行时规则。


