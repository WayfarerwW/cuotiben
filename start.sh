#!/usr/bin/env bash
# 错题本 · Linux / macOS 启动脚本
# 用法： ./start.sh
# 需要已安装 Python 并装好依赖：pip install -r requirements.txt

set -e

# 切到脚本所在目录，保证在任意路径下执行都能找到 run.py
cd "$(dirname "$0")"

exec python3 run.py
