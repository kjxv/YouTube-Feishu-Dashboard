#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

REPO_URL="${YFD_REPO_URL:-https://github.com/kjxv/YouTube-Feishu-Dashboard.git}"
BRANCH="${YFD_BRANCH:-main}"
ARCHIVE_URL="${YFD_ARCHIVE_URL:-https://codeload.github.com/kjxv/YouTube-Feishu-Dashboard/tar.gz/refs/heads/${BRANCH}}"
INSTALL_DIR="${YFD_INSTALL_DIR:-/opt/YouTube-Feishu-Dashboard}"
ACTION="${1:-auto}"
SERVICE_NAME="yfd-tick.service"
TIMER_NAME="yfd-tick.timer"

if [[ "${INSTALL_DIR}" =~ [[:space:]%] ]]; then
  echo "安装路径不能包含空格或百分号：${INSTALL_DIR}" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
  RUN_USER="${YFD_RUN_USER:-${SUDO_USER:-root}}"
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "一键部署需要 root 权限或 sudo。" >&2
    exit 1
  fi
  SUDO=(sudo)
  RUN_USER="${YFD_RUN_USER:-$(id -un)}"
fi

if ! id "${RUN_USER}" >/dev/null 2>&1; then
  echo "指定的运行用户不存在：${RUN_USER}" >&2
  exit 1
fi
RUN_GROUP="$(id -gn "${RUN_USER}")"

as_root() {
  "${SUDO[@]}" "$@"
}

as_run_user() {
  if [[ "$(id -un)" == "${RUN_USER}" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u "${RUN_USER}" -H "$@"
  else
    runuser -u "${RUN_USER}" -- "$@"
  fi
}

python_is_supported() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    >/dev/null 2>&1
}

select_python() {
  local candidate
  if [[ -n "${YFD_PYTHON_BIN:-}" ]]; then
    if ! command -v "${YFD_PYTHON_BIN}" >/dev/null 2>&1 || \
      ! python_is_supported "${YFD_PYTHON_BIN}"; then
      echo "YFD_PYTHON_BIN 不存在或版本低于 Python 3.11：${YFD_PYTHON_BIN}" >&2
      exit 1
    fi
    PYTHON_BIN="$(command -v "${YFD_PYTHON_BIN}")"
    return
  fi

  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_is_supported "${candidate}"; then
      PYTHON_BIN="$(command -v "${candidate}")"
      return
    fi
  done

  echo "未找到 Python 3.11 或更高版本。" >&2
  echo "建议使用 Debian 12 或 Ubuntu 24.04；安装合适版本后重新执行同一条命令。" >&2
  exit 1
}

install_system_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "当前一键脚本仅支持使用 apt 的 Debian/Ubuntu 系统。" >&2
    exit 1
  fi
  echo "[1/7] 安装 VPS 基础依赖..."
  as_root apt-get update
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl git python3 python3-venv
}

timer_is_installed() {
  [[ -f "/etc/systemd/system/${TIMER_NAME}" ]]
}

pause_existing_timer() {
  if ! timer_is_installed; then
    return
  fi
  echo "检测到已有 VPS 定时任务，更新前先安全暂停..."
  as_root systemctl disable --now "${TIMER_NAME}" >/dev/null
  if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "当前仍有一次采集正在运行。已阻止后续触发，请等待本轮结束后重新执行部署命令。" >&2
    exit 1
  fi
}

backup_existing_database() {
  local backup_script="${INSTALL_DIR}/01_启动脚本/Linux_VPS/备份SQLite数据库.sh"
  if [[ -x "${INSTALL_DIR}/runtime/venv/bin/python" && \
        -f "${INSTALL_DIR}/.env" && \
        -f "${backup_script}" ]]; then
    echo "更新前备份现有 SQLite 数据库..."
    as_run_user bash "${backup_script}"
  fi
}

sync_repository() {
  echo "[2/7] 下载或更新 GitHub 项目..."
  as_root mkdir -p "${INSTALL_DIR}"
  as_root chown "${RUN_USER}:${RUN_GROUP}" "${INSTALL_DIR}"

  if [[ -d "${INSTALL_DIR}/.git" ]] && \
    as_run_user git -C "${INSTALL_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [[ -n "$(as_run_user git -C "${INSTALL_DIR}" status --porcelain --untracked-files=no)" ]]; then
      echo "VPS 项目中存在未提交的代码修改，为避免覆盖已停止自动更新。" >&2
      echo "请先处理 ${INSTALL_DIR} 中的修改，再重新执行。" >&2
      exit 1
    fi
    if as_run_user git -C "${INSTALL_DIR}" fetch origin "${BRANCH}" && \
      as_run_user git -C "${INSTALL_DIR}" checkout "${BRANCH}" && \
      as_run_user git -C "${INSTALL_DIR}" pull --ff-only origin "${BRANCH}"; then
      return
    fi
    echo "连接 github.com 的 Git 服务失败，改用 GitHub 官方源码包..."
    sync_official_archive
    return
  fi

  if [[ -n "$(find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" && \
        ! -f "${INSTALL_DIR}/pyproject.toml" ]]; then
    echo "安装目录中存在无法识别的文件：${INSTALL_DIR}" >&2
    echo "请不要手工删除；先检查目录内容再处理。" >&2
    exit 1
  fi

  if [[ -z "$(find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]] && \
    as_run_user git clone --branch "${BRANCH}" --single-branch "${REPO_URL}" "${INSTALL_DIR}"; then
    return
  fi

  echo "连接 github.com 的 Git 服务失败，改用 GitHub 官方源码包..."
  sync_official_archive
}

sync_official_archive() {
  local temp_dir archive_path source_dir
  temp_dir="$(mktemp -d)"
  archive_path="${temp_dir}/source.tar.gz"

  if ! curl -4 -fL --retry 4 --retry-delay 3 --retry-all-errors \
    --connect-timeout 20 "${ARCHIVE_URL}" -o "${archive_path}"; then
    rm -rf -- "${temp_dir}"
    echo "GitHub 官方源码包也无法下载。请检查 VPS 出站网络或代理配置。" >&2
    exit 1
  fi

  tar -xzf "${archive_path}" -C "${temp_dir}"
  source_dir="$(find "${temp_dir}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
  if [[ -z "${source_dir}" || ! -f "${source_dir}/pyproject.toml" ]]; then
    rm -rf -- "${temp_dir}"
    echo "下载的源码包结构不正确，已经停止部署。" >&2
    exit 1
  fi

  as_run_user cp -a "${source_dir}/." "${INSTALL_DIR}/"
  as_run_user touch "${INSTALL_DIR}/.yfd-source-archive"
  rm -rf -- "${temp_dir}"
}

prepare_runtime() {
  echo "[3/7] 创建 Python 环境并安装项目..."
  as_run_user mkdir -p \
    "${INSTALL_DIR}/data" \
    "${INSTALL_DIR}/secrets" \
    "${INSTALL_DIR}/runtime"

  if [[ ! -x "${INSTALL_DIR}/runtime/venv/bin/python" ]]; then
    as_run_user "${PYTHON_BIN}" -m venv "${INSTALL_DIR}/runtime/venv"
  elif ! python_is_supported "${INSTALL_DIR}/runtime/venv/bin/python"; then
    echo "现有 runtime/venv 的 Python 版本低于 3.11。" >&2
    echo "请先把该目录改名留作备份，再重新执行部署命令。" >&2
    exit 1
  fi

  as_run_user "${INSTALL_DIR}/runtime/venv/bin/python" -m pip install --upgrade \
    "${INSTALL_DIR}"
  as_root chown -R "${RUN_USER}:${RUN_GROUP}" "${INSTALL_DIR}"
  as_run_user chmod +x "${INSTALL_DIR}"/01_启动脚本/Linux_VPS/*.sh
}

missing_production_files() {
  local path missing=0
  for path in \
    ".env" \
    "secrets/youtube_client_secret.json" \
    "secrets/youtube_token.json" \
    "data/yfd.db"; do
    if [[ ! -s "${INSTALL_DIR}/${path}" ]]; then
      echo "  - ${INSTALL_DIR}/${path}"
      missing=1
    fi
  done
  return "${missing}"
}

show_upload_instructions() {
  echo
  echo "预部署已经完成。请先在 Windows 暂停定时任务并确认当前没有任务运行，"
  echo "再通过 WinSCP/SFTP 上传以下四个生产文件："
  missing_production_files || true
  echo
  echo "上传完成后，再执行同一条一键部署命令；脚本会自动进入验收和启用阶段。"
  echo "注意：不要把这些文件提交到 GitHub。"
}

confirm_windows_paused() {
  local answer
  if timer_is_installed; then
    return
  fi
  if [[ "${YFD_WINDOWS_PAUSED:-}" == "YES" ]]; then
    return
  fi
  echo
  if [[ -r /dev/tty ]]; then
    read -r -p "确认 Windows 定时任务已暂停，并且当前没有采集正在运行？[y/N] " \
      answer </dev/tty
  else
    echo "无法进行交互确认。确认 Windows 已暂停后，用以下方式重试：" >&2
    echo "curl ... | YFD_WINDOWS_PAUSED=YES bash" >&2
    exit 1
  fi
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) echo "已取消启用 VPS 定时任务。"; exit 0 ;;
  esac
}

check_windows_proxy() {
  if grep -Eq \
    '^[[:space:]]*YFD_HTTPS_PROXY=.*127\.0\.0\.1:10808' \
    "${INSTALL_DIR}/.env" && [[ "${YFD_ALLOW_LOCAL_PROXY:-}" != "YES" ]]; then
    echo "检测到 Windows 本机代理 YFD_HTTPS_PROXY=...127.0.0.1:10808。" >&2
    echo "如果 VPS 可以直连 YouTube，请把 .env 中该值清空后重试。" >&2
    echo "只有 VPS 本机也运行该代理时，才可设置 YFD_ALLOW_LOCAL_PROXY=YES 放行。" >&2
    exit 1
  fi
}

secure_production_files() {
  echo "[4/7] 校验生产文件并收紧权限..."
  as_root chown -R "${RUN_USER}:${RUN_GROUP}" "${INSTALL_DIR}"
  as_run_user chmod 600 \
    "${INSTALL_DIR}/.env" \
    "${INSTALL_DIR}/secrets/youtube_client_secret.json" \
    "${INSTALL_DIR}/secrets/youtube_token.json" \
    "${INSTALL_DIR}/data/yfd.db"
}

run_acceptance() {
  echo "[5/7] 备份数据库并执行升级验收..."
  as_run_user bash "${INSTALL_DIR}/01_启动脚本/Linux_VPS/备份SQLite数据库.sh"
  as_run_user bash "${INSTALL_DIR}/01_启动脚本/Linux_VPS/升级并验收.sh"

  echo "[6/7] 分别执行一次最新视频与频道数据真实测试..."
  as_run_user bash "${INSTALL_DIR}/01_启动脚本/Linux_VPS/运行一次_最新视频.sh"
  as_run_user bash "${INSTALL_DIR}/01_启动脚本/Linux_VPS/运行一次_频道数据.sh"
}

enable_timer() {
  echo "[7/7] 安装并启用统一 systemd 定时任务..."
  if [[ "$(id -u)" -eq 0 ]]; then
    YFD_RUN_USER="${RUN_USER}" \
      bash "${INSTALL_DIR}/01_启动脚本/Linux_VPS/自动任务管理.sh" install
  else
    as_run_user bash "${INSTALL_DIR}/01_启动脚本/Linux_VPS/自动任务管理.sh" install
  fi
  echo
  echo "VPS 部署完成。Windows 定时任务必须继续保持暂停。"
  echo "以后查看状态："
  echo "  bash ${INSTALL_DIR}/01_启动脚本/Linux_VPS/自动任务管理.sh status"
  echo "查看最近日志："
  echo "  journalctl -u ${SERVICE_NAME} -n 200 --no-pager"
}

prepare() {
  install_system_packages
  select_python
  pause_existing_timer
  backup_existing_database
  sync_repository
  prepare_runtime
}

finish() {
  if ! missing_production_files >/dev/null; then
    show_upload_instructions
    exit 0
  fi
  confirm_windows_paused
  check_windows_proxy
  secure_production_files
  run_acceptance
  enable_timer
}

case "${ACTION}" in
  auto)
    prepare
    finish
    ;;
  prepare)
    prepare
    show_upload_instructions
    ;;
  finish)
    if [[ ! -d "${INSTALL_DIR}/.git" ]]; then
      echo "尚未完成预部署，请先执行 auto 或 prepare。" >&2
      exit 1
    fi
    select_python
    finish
    ;;
  *)
    echo "用法：deploy-vps.sh [auto|prepare|finish]" >&2
    exit 2
    ;;
esac
