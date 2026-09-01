#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ ! -x "runtime/venv/bin/python" ]]; then
  python3 -m venv runtime/venv
fi

runtime/venv/bin/python -m pip install --upgrade .
runtime/venv/bin/python -m youtube_feishu_dashboard setup --interactive

echo "首次配置基础步骤已完成。接下来请完成 YouTube 授权并运行系统检查。"
