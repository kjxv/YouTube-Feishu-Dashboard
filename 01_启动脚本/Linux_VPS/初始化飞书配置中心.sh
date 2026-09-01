#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$PROJECT_DIR"
runtime/venv/bin/python -m youtube_feishu_dashboard feishu bootstrap-config
printf '%s\n' '若显示成功，四张公共配置表的 Table ID 已写入 .env。'
