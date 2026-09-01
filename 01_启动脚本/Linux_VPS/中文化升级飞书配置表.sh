#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$PROJECT_DIR"
runtime/venv/bin/python -m youtube_feishu_dashboard feishu localize-config
printf '%s\n' '中文化升级完成后，可在飞书中查看中文名称、API来源和用途说明。'
