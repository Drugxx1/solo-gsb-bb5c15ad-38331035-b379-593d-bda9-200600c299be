#!/usr/bin/env bash
# 单条启动命令：完成（容器场景下的）初始化检查并启动 API。
# 依赖安装已在镜像构建阶段（Dockerfile RUN pip install）完成；
# 本地直接运行本脚本（./docker/entrypoint.sh）时会自动创建虚拟环境并安装依赖。
set -euo pipefail

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"

if [ ! -d "/.dockerenv" ] && [ ! -f "/app/app/main.py" ]; then
    # 本地运行：仓库根目录下自动准备虚拟环境与依赖
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
    cd "${REPO_ROOT}"
    if [ ! -d ".venv" ]; then
        echo "[entrypoint] 初始化虚拟环境 .venv ..."
        python3 -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "[entrypoint] 安装/校验依赖 ..."
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
else
    if [ -d "/app" ]; then
        cd /app
    fi
fi

echo "[entrypoint] 启动冷库温度越限复盘 API: http://${APP_HOST}:${APP_PORT}"
exec uvicorn app.main:app --host "${APP_HOST}" --port "${APP_PORT}"
