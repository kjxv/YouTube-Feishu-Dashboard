# Linux VPS 7×24 小时部署说明

## 一、当前推荐方案

当前正式方案是“一台 Linux VPS + systemd timer + SQLite”。Windows 运行电脑与 VPS
不能同时执行采集；开发电脑只改代码和推送 Git，不运行生产定时任务。

systemd 在每个整点调用一次统一入口 `scheduler tick`。这只是唤醒程序检查到期状态，
不代表每个模块在每个整点都请求 YouTube 或写飞书。最新视频实时追踪和频道每日统计仍按照飞书
配置、本地数据库游标和北京时间规则决定是否真正运行。

服务器控制台可能使用 UTC，例如 `07:00 UTC` 等于北京时间 `15:00`。这只影响命令行的
显示方式，程序与飞书状态字段仍统一使用 `Asia/Shanghai`。因为北京时间与 UTC 相差整数小时，
两边的整点一致；非整点分钟不是时区造成的。

Docker/PostgreSQL 文件目前只作为后续扩展模板。单机阶段先使用已经完整测试过的
SQLite，等需要多节点或数据库明显增大后，再单独安排 PostgreSQL 迁移验收。

## 二、VPS 要求

- 64 位 Linux，支持 systemd；Ubuntu 22.04/24.04 或 Debian 12 均可。
- Python 3.11 或更高版本、`python3-venv`、Git 和 CA 证书。
- 能直接访问 Google/YouTube 与飞书开放平台；系统时间同步正常。
- 建议至少 1 核 CPU、1 GB 内存和 10 GB 可用磁盘。
- 建议项目目录：`/opt/YouTube-Feishu-Dashboard`，路径不要含空格或 `%`。

本项目只主动向 YouTube 和飞书发起 HTTPS 请求，不对外提供网页服务，因此无需为它开放
新的入站端口。

如果 VPS 可以直接访问 YouTube，请把 `.env` 中 Windows 专用的
`YFD_HTTPS_PROXY=http://127.0.0.1:10808` 清空。只有 VPS 自己也运行同一代理时才保留，
且 `127.0.0.1` 永远指向 VPS 本机，不是原来的 Windows 电脑。

## 三、从 Windows 迁移的文件

Git 仓库不包含生产配置和运行状态。暂停 Windows 自动任务后，必须另外安全迁移：

- `.env`
- `secrets/youtube_client_secret.json`
- `secrets/youtube_token.json`
- `.env` 中 `YFD_DATABASE_URL` 指向的 SQLite 数据库，默认是 `data/yfd.db`

数据库必须一起迁移，因为里面保存调度游标、飞书绑定、任务锁状态和真实历史快照；
只拉 Git 后创建空数据库，会丢失这些本地状态，也无法补造旧视频的 48 小时节点。

不要通过 GitHub、聊天或公开网盘传输这些文件。上传 VPS 后执行：

```bash
cd /opt/YouTube-Feishu-Dashboard
sudo chown -R 你的Linux用户名:你的Linux用户组 /opt/YouTube-Feishu-Dashboard
chmod 600 .env secrets/youtube_client_secret.json secrets/youtube_token.json data/yfd.db
chmod +x 01_启动脚本/Linux_VPS/*.sh
```

项目目录、`data/`、`runtime/` 和上述文件必须属于最终运行 systemd 服务的用户，否则程序
可能能读取配置，却无法更新 Token、SQLite 数据库或运行状态。

## 四、首次上线顺序

### 一键部署入口（推荐）

仓库根目录提供了 `deploy-vps.sh`。如果 GitHub 仓库可公开读取，在 Debian 12 或
Ubuntu 24.04 VPS 上可以执行：

```bash
curl -fsSL https://raw.githubusercontent.com/kjxv/YouTube-Feishu-Dashboard/main/deploy-vps.sh | bash
```

第一次执行会自动安装基础依赖、下载项目、创建 Python 虚拟环境，然后在缺少生产文件时
安全停止。按照提示上传 `.env`、两个 YouTube OAuth 文件及 `data/yfd.db` 后，再执行完全
相同的命令。第二次会自动执行数据库备份、升级验收、两项真实同步测试并启用统一 systemd
timer。首次启用前脚本会要求确认 Windows 任务已经暂停。

如果 VPS 无法连接 `github.com:443`，脚本会自动改用 GitHub 官方的 `codeload.github.com`
源码包继续安装。两种官方地址都无法访问时才会停止，并且不会启用定时任务。源码包模式仍可
重复执行同一条命令完成部署和后续更新。

脚本会为 Linux 启动文件补充执行权限，并在 VPS 本地关闭 Git 的文件权限差异检测；纯粹的
可执行权限变化不会被误判为代码修改，真实的代码内容修改仍会阻止自动覆盖。

可用环境变量：`YFD_RUN_USER` 指定服务运行用户，`YFD_INSTALL_DIR` 修改安装目录，
`YFD_REPO_URL` 修改仓库地址，`YFD_BRANCH` 修改分支。若仓库为私有仓库，应先在 VPS 配置
只读 SSH 密钥，再通过 `YFD_REPO_URL=git@github.com:...` 运行本地脚本；不要把 GitHub
令牌直接写进命令历史。

一键脚本不会下载或生成任何密钥，也不会将生产文件上传 GitHub。检测到 Windows 专用的
`127.0.0.1:10808` 代理时会停止，要求先确认 VPS 的代理配置。

### 1. 先停止旧执行端

在 Windows 运行电脑打开 `10.自动定时任务管理.bat`，选择“暂停”。确认旧任务不再触发后，
再复制上面的四类生产文件。开发电脑无需停用，只要它本来就不运行采集。

### 2. 安装项目

以最终负责运行任务的普通 Linux 用户执行：

```bash
cd /opt/YouTube-Feishu-Dashboard
python3 -m venv runtime/venv
runtime/venv/bin/python -m pip install --upgrade .
```

如果这是完全新建的账号环境，而不是从 Windows 迁移，再运行
`bash 01_启动脚本/Linux_VPS/首次配置.sh`。正常迁移不要让首次配置覆盖已复制的 `.env`。

### 3. 升级并只读验收

```bash
bash 01_启动脚本/Linux_VPS/升级并验收.sh
```

该脚本会安装当前代码、升级数据库、检查在线连接、幂等确认 48 小时字段，并只读检查
两个模块。它不会采集 YouTube 数据，也不会改变频道模块开关。任何一步失败都不要安装定时任务。

### 4. 人工验证两个真实任务

```bash
bash 01_启动脚本/Linux_VPS/运行一次_最新视频.sh
bash 01_启动脚本/Linux_VPS/运行一次_频道数据.sh
```

随后在飞书确认时间和数据正常。频道任务开关如果已经在 Windows 环境开启，不需要再次开启；
飞书配置中心是两个系统共用的。

### 5. 安装统一定时任务

不要用 `sudo bash` 启动整个脚本；以目标普通用户执行，让脚本在需要时调用 sudo：

```bash
bash 01_启动脚本/Linux_VPS/自动任务管理.sh install
```

如果必须从 root 会话安装，请明确指定实际运行用户：

```bash
YFD_RUN_USER=你的Linux用户名 bash 01_启动脚本/Linux_VPS/自动任务管理.sh install
```

安装成功后，两个业务模块共用 `yfd-tick.timer`，不要再为频道模块单独创建 cron 或 timer。

## 五、日常管理与排错

```bash
# 查看计时器、服务和下次唤醒时间
bash 01_启动脚本/Linux_VPS/自动任务管理.sh status

# 立即执行一次统一到期检查，并显示最近日志
bash 01_启动脚本/Linux_VPS/自动任务管理.sh run-now

# 暂停或恢复
bash 01_启动脚本/Linux_VPS/自动任务管理.sh pause
bash 01_启动脚本/Linux_VPS/自动任务管理.sh resume

# 持续查看采集日志
journalctl -u yfd-tick.service -f

# 查看最近 200 行日志
journalctl -u yfd-tick.service -n 200 --no-pager
```

`yfd-tick.service` 是一次性任务，平时显示 `inactive (dead)` 是正常的；关键是 timer 应为
`active (waiting)`，最近一次 service 退出码应为 0。`run-now` 运行的是“到期检查”，
如果业务任务尚未到期，成功但没有飞书数据变化也是正常的。

每次从 GitHub 更新代码后执行：

```bash
cd /opt/YouTube-Feishu-Dashboard
bash 01_启动脚本/Linux_VPS/自动任务管理.sh pause
bash 01_启动脚本/Linux_VPS/备份SQLite数据库.sh
git pull --ff-only
bash 01_启动脚本/Linux_VPS/升级并验收.sh
bash 01_启动脚本/Linux_VPS/自动任务管理.sh install
```

## 六、备份与回退

手工备份：

```bash
bash 01_启动脚本/Linux_VPS/备份SQLite数据库.sh
```

脚本使用 SQLite 在线备份接口，并做完整性检查，文件保存在 `runtime/backups/vps/`。
它不会自动删除旧备份。建议每天把最新备份再复制到 VPS 之外的私有位置。

回退到 Windows 时：先暂停 VPS timer，确认没有正在运行的 service，把最新 SQLite 数据库
和必要配置安全复制回 Windows，再恢复 Windows 计划任务。绝不能让两个执行端交叉运行。

## 七、验收标准

满足以下条件后，VPS 迁移才算完成：

1. `升级并验收.sh` 全部通过。
2. 最新视频和频道数据各手动运行一次成功，飞书数据正确。
3. `yfd-tick.timer` 显示 `active (waiting)`，能看到下次唤醒时间。
4. 手工 `run-now` 后 service 退出码为 0，日志没有鉴权或数据库错误。
5. SQLite 备份生成且完整性检查通过。
6. Windows 生产计划任务保持暂停；稳定观察 2～3 天后再决定是否卸载。
