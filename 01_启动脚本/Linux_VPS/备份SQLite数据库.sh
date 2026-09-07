#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON="${PROJECT_ROOT}/runtime/venv/bin/python"
BACKUP_DIR="${PROJECT_ROOT}/runtime/backups/vps"

cd "${PROJECT_ROOT}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "未找到 runtime/venv，无法读取项目数据库配置。" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo "未找到 .env，无法确定数据库位置。" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"

"${PYTHON}" - "${PROJECT_ROOT}" "${BACKUP_DIR}" <<'PY'
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from youtube_feishu_dashboard.core.settings import Settings

project_root = Path(sys.argv[1]).resolve()
backup_dir = Path(sys.argv[2]).resolve()
settings = Settings.load(project_root)
url = make_url(settings.sqlalchemy_url)

if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
    raise SystemExit("当前不是文件型 SQLite 数据库；本脚本没有执行任何操作。")

source_path = Path(url.database).resolve()
if not source_path.is_file():
    raise SystemExit(f"找不到 SQLite 数据库：{source_path}")

stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
target_path = backup_dir / f"yfd_{stamp}.db"

with sqlite3.connect(source_path, timeout=30) as source, sqlite3.connect(
    target_path, timeout=30
) as target:
    source.backup(target)

with sqlite3.connect(target_path) as check:
    result = check.execute("PRAGMA integrity_check").fetchone()

if result is None or result[0] != "ok":
    target_path.unlink(missing_ok=True)
    raise SystemExit("备份完整性检查失败，损坏的备份文件已删除。")

os.chmod(target_path, 0o600)
print(f"SQLite 在线备份完成：{target_path}")
PY
