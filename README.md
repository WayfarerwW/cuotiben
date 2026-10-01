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
| 前端 | Vue 3 + Element Plus（响应式） |
| 图片处理 | Pillow + pillow-heif |
| PDF 导出 | WeasyPrint + Jinja2 |
| 启动 | uvicorn，浏览器访问 `http://localhost:8000` |

## 目录结构

```
cuotiben/
├── docs/                  # 需求文档 + UI 说明书（只读，不改）
├── AGENTS.md              # Agent 工作规则
├── app/                   # 后端 + 前端
│   ├── routers/           # 路由（只做参数校验与响应）
│   ├── services/          # 业务逻辑
│   ├── models/            # ORM 模型
│   ├── templates/         # PDF 模板
│   └── static/            # 前端资源
│       ├── css/
│       └── js/
├── data/                  # SQLite 数据库
├── uploads/               # 图片（按年月日分片，文件名 UUID）
├── backups/               # 数据库每日备份
├── fonts/                 # 中文字体（PDF 导出用）
├── requirements.txt
└── README.md
```

## 安装与运行

```bash
# 1. 创建虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动（启动脚本 run.py 随后端实现一并添加）
python run.py
```

启动后浏览器访问 <http://localhost:8000>。

> 当前仓库处于**初始化阶段**：仅完成目录骨架与依赖清单，业务代码尚未实现。

## 文档

实现任何功能前先阅读对应章节，不要凭记忆实现：

| 文档 | 内容 |
|---|---|
| [docs/requirements.md](docs/requirements.md) | 功能、数据模型、接口、关键逻辑、验收标准 |
| [docs/ui-design.md](docs/ui-design.md) | 色彩、字体、间距、组件、交互、响应式 |
| [AGENTS.md](AGENTS.md) | 代码规范、关键规则（打勾逻辑、时区、命名等） |

## 数据位置

| 内容 | 路径 |
|---|---|
| 数据库 | `data/cuotiben.db` |
| 图片 | `uploads/YYYY/MM/DD/{uuid}.jpg` |
| 备份 | `backups/` |
| 中文字体 | `fonts/` |
