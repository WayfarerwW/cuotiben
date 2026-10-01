"""开发/本地启动入口。

用法：
    python run.py                # 正常启动（默认）
    python run.py --dev          # 开发模式：改代码自动重启
    python run.py --port 9000    # 换端口
    python run.py --host 0.0.0.0 # 允许局域网访问（见下）

然后浏览器打开 http://localhost:8000

两个刻意的取舍：

1. **默认不开 reload。** 之前默认 `reload=True`，对最终用户是错的：
   它让 uvicorn 监视整个项目目录，任何文件变动都会重启进程 ——
   包括 `logs/`、临时文件，以及 GitHub 自动同步（AGENTS.md 五）
   自己产生的改动；后台同步线程被反复杀掉重启，同步会变得不可靠。
   改代码自动重启只在开发时有用，所以挪到 `--dev`。

2. **host 默认 127.0.0.1。** 本项目是**纯本地单机**应用（AGENTS.md 一），
   不对外提供服务。确实需要用手机在局域网里访问时，用
   `--host 0.0.0.0` 显式开启 —— 但那会让同网段的机器也能读写你的题库，
   请自行确认网络环境。
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="错题本 · 纯本地单机版启动入口",
    )
    parser.add_argument("--dev", action="store_true",
                        help="开发模式：改代码自动重启（会监视整个项目目录）")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"监听地址，默认 {DEFAULT_HOST}；"
                             "用 0.0.0.0 允许局域网访问")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"监听端口，默认 {DEFAULT_PORT}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print("缺少依赖 uvicorn，请先执行：pip install -r requirements.txt")
        return 1

    shown_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print("=" * 60)
    print("错题本 · 纯本地单机版")
    print(f"启动后请用浏览器打开： http://{shown_host}:{args.port}")
    if args.host == "0.0.0.0":
        print("⚠ 已监听 0.0.0.0：同网段的机器也能访问并读写你的题库")
    if args.dev:
        print("开发模式：改代码会自动重启")
    print("按 Ctrl+C 停止服务")
    print("=" * 60)

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.dev,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
