#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON="${PROJECT_ROOT}/runtime/venv/bin/python"

cd "${PROJECT_ROOT}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "未找到 runtime/venv。请先运行 Linux_VPS/首次配置.sh。" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo "未找到 .env。请先从运行电脑安全迁移配置，或运行首次配置脚本。" >&2
  exit 1
fi

echo "[1/7] 安装当前项目版本..."
"${PYTHON}" -m pip install --upgrade .

echo "[2/7] 升级本地数据库结构..."
"${PYTHON}" -m youtube_feishu_dashboard db upgrade head

echo "[3/7] 检查配置、凭证、数据库和飞书连接..."
"${PYTHON}" -m youtube_feishu_dashboard doctor --online

echo "[4/7] 幂等创建或补齐系统运行状态表..."
"${PYTHON}" -m youtube_feishu_dashboard feishu enable-runtime-status

echo "[5/7] 幂等确认视频追踪1至72小时节点字段和共享映射..."
"${PYTHON}" -m youtube_feishu_dashboard feishu enable-latest-milestone-fields

echo "[6/7] 幂等确认频道48小时字段和共享映射..."
"${PYTHON}" -m youtube_feishu_dashboard feishu enable-channel-48h-fields

echo "[7/7] 只读检查最新视频与频道数据模块..."
"${PYTHON}" -m youtube_feishu_dashboard modules validate-sync latest_video_tracker
"${PYTHON}" -m youtube_feishu_dashboard modules validate-sync channel_history

echo
echo "升级和只读验收完成。未执行采集，也未改变模块开关。"
