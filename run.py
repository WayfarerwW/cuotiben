"""开发/本地启动入口。

用法：
    python run.py

然后浏览器打开 http://localhost:8000

为什么 host 固定 127.0.0.1：本项目是**纯本地单机**应用（AGENTS.md 一），
不对外提供服务。确实需要用手机在局域网里访问时，再手动改成 0.0.0.0 —— 
但那会让同网段的机器也能读写你的题库，请自行确认网络环境。
"""

from __future__ import annotations

import sys

HOST = "127.0.0.1"
PORT = 8000


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("缺少依赖 uvicorn，请先执行：pip install -r requirements.txt")
        return 1

    print("=" * 60)
    print("错题本 · 纯本地单机版")
    print(f"启动后请用浏览器打开： http://localhost:{PORT}")
    print("按 Ctrl+C 停止服务")
    print("=" * 60)

    # reload=True：改代码自动重启，方便开发。
    # 注意 reload 会以子进程方式再启动一次本模块，
    # 因此这里不能放需要在父进程完成的一次性初始化。
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
