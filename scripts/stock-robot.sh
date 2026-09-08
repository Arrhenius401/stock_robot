#!/usr/bin/env sh
# 在不激活虚拟环境的情况下，使用项目自己的 stock-robot 命令。
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
EXECUTABLE="$PROJECT_ROOT/.venv/bin/stock-robot"

if [ ! -x "$EXECUTABLE" ]; then
  echo "未找到项目虚拟环境。请先执行：python3 -m venv .venv；随后执行：./.venv/bin/python -m pip install -e '.[dev]'" >&2
  exit 1
fi

exec "$EXECUTABLE" "$@"
