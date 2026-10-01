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

启动后浏览器访问 <http://localhost:8000>。服务监听 `127.0.0.1`（纯本地单机），
改代码会自动重启（uvicorn `reload=True`）。

> 想确认"启动后确实能打开、没有 404、Vue 正常挂载"，
> 可运行 `python tools/verify_launch.py`（会用临时库真实启动一次并逐项检查）。

### 环境自检

```bash
python tools/verify_launch.py          # 启动可访问性：run.py 起服务、静态资源无 404、Vue 挂载
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

> 这两个工具名以下划线开头，是**测试脚手架**，不是产品代码：
> 真实部署时前端应当由 `app/main.py` 直接提供（目前尚未挂载，见「已知缺口」）。

## 文档

实现任何功能前先阅读对应章节，不要凭记忆实现：

| 文档 | 内容 |
|---|---|
| [docs/requirements.md](docs/requirements.md) | 功能、数据模型、接口、关键逻辑、验收标准 |
| [docs/ui-design.md](docs/ui-design.md) | 色彩、字体、间距、组件、交互、响应式 |
| [docs/poc-weasyprint.md](docs/poc-weasyprint.md) | PoC 结论：WeasyPrint 中文渲染、字体选型、HEIC 读写 |
| [AGENTS.md](AGENTS.md) | 代码规范、关键规则（打勾逻辑、时区、命名等） |

## 数据位置

| 内容 | 路径 |
|---|---|
| 数据库 | `data/cuotiben.db` |
| 图片 | `uploads/YYYY/MM/DD/{uuid}.jpg` |
| 备份 | `backups/` |
| 中文字体 | `fonts/NotoSansSC-VF.ttf`（Noto Sans SC，SIL OFL 1.1） |
| 环境自检脚本 | `tools/` |

