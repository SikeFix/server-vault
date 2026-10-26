#!/usr/bin/env bash
# 一键启动 Server Vault:创建虚拟环境、安装依赖、启动服务(默认 http://127.0.0.1:8765)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  if command -v uv >/dev/null 2>&1; then uv venv -q .venv; else python3 -m venv .venv; fi
fi
if ! .venv/bin/python -c "import fastapi, paramiko, cryptography, uvicorn" 2>/dev/null; then
  if command -v uv >/dev/null 2>&1; then uv pip install -q -p .venv/bin/python -r requirements.txt
  else .venv/bin/pip install -q -r requirements.txt; fi
fi
HOST="${SV_HOST:-127.0.0.1}"
PORT="${SV_PORT:-8780}"
echo "Server Vault → http://${HOST}:${PORT}"
exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
