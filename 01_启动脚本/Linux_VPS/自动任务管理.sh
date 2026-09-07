#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
PYTHON="${PROJECT_ROOT}/runtime/venv/bin/python"
SERVICE_NAME="yfd-tick.service"
TIMER_NAME="yfd-tick.timer"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "${PROJECT_ROOT}" =~ [[:space:]%] ]]; then
  echo "项目路径不能含空格或百分号。VPS 建议安装到 /opt/YouTube-Feishu-Dashboard。" >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "当前系统没有 systemd/systemctl，不能安装此定时任务。" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
  RUN_USER="${YFD_RUN_USER:-${SUDO_USER:-root}}"
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "安装系统任务需要 root 权限或 sudo。" >&2
    exit 1
  fi
  SUDO=(sudo)
  RUN_USER="${YFD_RUN_USER:-$(id -un)}"
fi

if ! id "${RUN_USER}" >/dev/null 2>&1; then
  echo "运行用户不存在：${RUN_USER}" >&2
  exit 1
fi
RUN_GROUP="$(id -gn "${RUN_USER}")"

require_runtime() {
  if [[ ! -x "${PYTHON}" ]]; then
    echo "未找到 runtime/venv。请先运行 Linux_VPS/首次配置.sh。" >&2
    exit 1
  fi
  if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
    echo "未找到 .env。请先完成配置迁移和只读验收。" >&2
    exit 1
  fi
}

install_or_update() {
  require_runtime
  "${SUDO[@]}" install -m 0644 /dev/stdin "${SYSTEMD_DIR}/${SERVICE_NAME}" <<EOF
[Unit]
Description=YouTube Feishu Dashboard scheduler tick
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_ROOT}
EnvironmentFile=${PROJECT_ROOT}/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m youtube_feishu_dashboard scheduler tick
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
TimeoutStartSec=2h
EOF

  "${SUDO[@]}" install -m 0644 /dev/stdin "${SYSTEMD_DIR}/${TIMER_NAME}" <<EOF
[Unit]
Description=Wake YouTube Feishu Dashboard scheduler at every full hour

[Timer]
OnCalendar=hourly
AccuracySec=1s
Persistent=true
Unit=${SERVICE_NAME}

[Install]
WantedBy=timers.target
EOF

  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl enable --now "${TIMER_NAME}"
  echo "统一定时任务已安装或更新。"
  show_status
}

show_status() {
  "${SUDO[@]}" systemctl --no-pager --full status "${TIMER_NAME}" || true
  "${SUDO[@]}" systemctl --no-pager --full status "${SERVICE_NAME}" || true
  "${SUDO[@]}" systemctl list-timers --all "${TIMER_NAME}" --no-pager || true
  echo "最近日志：journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
}

pause_timer() {
  "${SUDO[@]}" systemctl disable --now "${TIMER_NAME}"
  echo "统一定时任务已暂停。"
}

resume_timer() {
  require_runtime
  "${SUDO[@]}" systemctl enable --now "${TIMER_NAME}"
  echo "统一定时任务已恢复。"
  show_status
}

run_now() {
  require_runtime
  "${SUDO[@]}" systemctl start "${SERVICE_NAME}"
  "${SUDO[@]}" journalctl -u "${SERVICE_NAME}" -n 100 --no-pager
}

uninstall_timer() {
  "${SUDO[@]}" systemctl disable --now "${TIMER_NAME}" 2>/dev/null || true
  "${SUDO[@]}" rm -f "${SYSTEMD_DIR}/${SERVICE_NAME}" "${SYSTEMD_DIR}/${TIMER_NAME}"
  "${SUDO[@]}" systemctl daemon-reload
  "${SUDO[@]}" systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true
  echo "统一定时任务已卸载；项目、配置、数据库和日志均未删除。"
}

ACTION="${1:-}"
if [[ -z "${ACTION}" ]]; then
  cat <<'EOF'
YouTube 飞书数据 VPS 自动任务管理
1. 安装或更新
2. 查看状态
3. 暂停
4. 恢复
5. 立即检查一次调度
6. 卸载系统任务
0. 退出
EOF
  read -r -p "请选择 (0-6): " CHOICE
  case "${CHOICE}" in
    1) ACTION="install" ;;
    2) ACTION="status" ;;
    3) ACTION="pause" ;;
    4) ACTION="resume" ;;
    5) ACTION="run-now" ;;
    6) ACTION="uninstall" ;;
    0) exit 0 ;;
    *) echo "无效选择。" >&2; exit 2 ;;
  esac
fi

case "${ACTION}" in
  install|update) install_or_update ;;
  status) show_status ;;
  pause) pause_timer ;;
  resume) resume_timer ;;
  run-now) run_now ;;
  uninstall) uninstall_timer ;;
  *)
    echo "用法：$0 [install|status|pause|resume|run-now|uninstall]" >&2
    exit 2
    ;;
esac
