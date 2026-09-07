# YouTube 数据中心 V1

这是一个面向长期扩展的 YouTube 数据采集、存储、调度和飞书同步项目。目前已经实现“最新发布视频实时追踪”和“频道每日统计（长视频）”，全视频当前数据等后续模块继续通过同一组公共接口接入。

## 最常用入口

- Windows 日常手动同步：双击 `01_启动脚本/Windows/6.运行一次_最新视频.bat`。
- 修改配置或字段后：先双击 `9.只读检查_三表配置.bat`，再双击 `8.只读预览_最新视频.bat`。
- 频道每日统计：先双击 `11.只读检查_频道数据.bat`；检查通过后可用 `12.运行一次_频道数据.bat` 手动验证。
- 自动运行：双击 `10.自动定时任务管理.bat`；两个已启用模块共用同一个 Windows 计划任务。
- 自动任务异常：双击 `16.诊断定时任务.bat`；飞书“系统运行状态”表用于观察每次唤醒和完成。
- 自动运行入口：`7.调度滴答.bat` 只供 Windows 计划任务调用，不是日常手动入口。
- VPS 7×24 运行：按 [Linux VPS 部署说明](00_项目文档/VPS部署说明.md) 迁移，两个模块共用一个 systemd timer。
- VPS 一键预部署及验收：运行仓库根目录的 `deploy-vps.sh`；生产配置、OAuth 文件和 SQLite 数据库仍需通过 SFTP 安全上传。
- 完整操作、字段调整和新增模块方法见 [日常使用与扩展指南](00_项目文档/日常使用与扩展指南.md)。

## 核心边界

- 公共核心框架统一管理 YouTube OAuth 与三个 API 客户端、飞书 HTTP、数据库、字段目录、调度、任务锁、日志、健康状态和归档。
- 功能模块只声明数据需求并实现业务流程，不自行保存 Token、不新建数据库、不直接请求飞书、不安装系统计划任务。
- 全部模块共用一个主数据库。V1 默认 SQLite，数据库访问统一经过 Repository/Storage 接口，并预留 PostgreSQL 驱动。
- Windows 计划任务、Linux cron/systemd 和未来容器只触发同一个 `yfd scheduler tick` 入口。
- 断电期间不伪造快照。恢复运行后只记录真实抓取时间，并根据数据库里的到期状态继续任务。

## 目录导航

```text
00_项目文档/                    设计、部署、开发规范与断点记录
01_启动脚本/                    Windows 与 Linux/VPS 中文入口
02_配置模板/                    飞书公共表配置模板

03_公共核心框架/                所有模块共用，业务模块不得复制这些能力
  src/youtube_feishu_dashboard/
    api/                        YouTube 三类 API、飞书和配置中心
    catalog/                    版本化 API 字段能力目录
    core/                       配置、日志、时间和错误类型
    db/                         公共数据库与 Repository/Storage
    scheduler/                  统一调度、任务锁、重试和恢复
    services/                   健康、归档、飞书记录等公共服务
    db/alembic_scripts/          随公共包发布的主数据库迁移

04_功能模块/                    每个业务板块都是独立 Python 包
  01_最新发布视频实时追踪/       第一阶段完整实现
  02_频道历史数据/               已实现的频道每日统计（仅长视频）
  03_全部视频当前数据/           后续模块模板

05_部署与运维/                  Docker、systemd 等服务器部署材料
90_开发与测试/                  自动化测试；不参与生产业务运行

data/ logs/ runtime/ secrets/   本地运行数据，全部被 Git 忽略
```

判断规则很简单：可被多个业务板块复用的代码放进 `03_公共核心框架`；只服务于一个业务目标的流程放进 `04_功能模块` 对应分区。每个分区中的 `README.md` 都说明了允许和禁止承担的职责。

## 快速开始（开发环境）

1. 安装 Python 3.11 或更高版本。
2. Windows 双击 `01_启动脚本/Windows/1.首次配置.bat`；Linux/VPS 运行 `bash 01_启动脚本/Linux_VPS/首次配置.sh`。
3. 将 Google OAuth 客户端 JSON 放到 `secrets/`，在向导生成的 `.env` 中填写飞书应用信息。
4. 运行 `yfd doctor --online`，再依次执行只读预览和三表同步前检查；两项都确认后才运行真实任务。

从现有 Windows 运行电脑迁移到 VPS 时，不要重新初始化空环境。应先暂停 Windows 计划任务，
安全复制 `.env`、两个 YouTube OAuth 文件及现有 SQLite 数据库，再运行
`Linux_VPS/升级并验收.sh`。具体步骤和日志命令见 VPS 部署说明。

当前可用命令：

```text
yfd setup [--interactive]
yfd auth youtube
yfd doctor [--online]
yfd db upgrade [revision]
yfd catalog sync|show|push-feishu
yfd feishu bootstrap-config [--no-write-env]
yfd feishu localize-config
yfd feishu mark-critical-fields
yfd feishu enable-runtime-status
yfd feishu enable-channel-48h-fields
yfd modules preview latest_video_tracker [--channel-id UC...]
yfd modules validate-sync latest_video_tracker
yfd modules validate-sync channel_history
yfd scheduler run-once latest-video-tracker [--dry-run]
yfd scheduler run-once channel-history-daily [--dry-run]
yfd scheduler tick
yfd scheduler status
yfd modules
```

`modules preview` 只读取 YouTube 并显示标准字段，不写飞书或业务数据库；OAuth
访问令牌过期时可能在本地安全刷新 Token 文件。`modules validate-sync` 只读取飞书配置、
三张业务表结构和当前实现状态，不修改飞书内容。只有检查报告中的
`safe_to_run_full_three_table_sync` 为 `true`，才表示三表完整同步已准备好。

`scheduler run-once --dry-run` 不访问外部服务，只输出请求规划和缺失的引导配置。当前实施
进度以 [开发进度与断点](00_项目文档/开发进度与断点.md) 和 `project_state.json` 为准。

## 当前模块状态

| 模块 | 状态 | 说明 |
|---|---|---|
| [最新发布视频实时追踪](04_功能模块/01_最新发布视频实时追踪/README.md) | 已实现 V1 | YouTube → 数据库 → 主表、快照表、同期对比表完整闭环 |
| [频道历史数据](04_功能模块/02_频道历史数据/README.md) | 已实现 V1 | 每天北京时间 08:00 同步频道基础数据、长视频主表和 Analytics 日统计 |
| [全部视频当前数据](04_功能模块/03_全部视频当前数据/README.md) | 接口模板 | 已声明字段需求，未注册任务 |

## Windows 与 VPS 运行速查

### 重要原则

- Windows 运行电脑和 VPS 只能启用一个，绝不能同时采集和写入飞书。
- 开发电脑只负责修改代码和推送 GitHub，不启用生产定时任务。
- `.env`、`secrets/`、`data/yfd.db` 不会进入 Git，必须通过 SFTP 等私密方式迁移。
- “系统运行状态”显示“正常｜本轮没有业务任务到期”，表示调度器正常，只是该轮无需采集。

### Windows 日常使用

在项目根目录的 `01_启动脚本/Windows/` 中双击对应文件：

| 目的 | 使用文件 | 说明 |
|---|---|---|
| 首次建立环境 | `1.首次配置.bat` | 仅全新环境使用 |
| 完成 YouTube 授权 | `2.YouTube授权.bat` | 生成或刷新 OAuth Token |
| 检查环境和联网 | `5.系统检查.bat` | 检查配置、数据库、YouTube 和飞书 |
| 手动同步最新视频 | `6.运行一次_最新视频.bat` | 会真实读取并写入数据 |
| 只读预览最新视频 | `8.只读预览_最新视频.bat` | 不写飞书业务数据 |
| 检查三张追踪表 | `9.只读检查_三表配置.bat` | 修改字段或映射后使用 |
| 安装、暂停、恢复或查看自动任务 | `10.自动定时任务管理.bat` | Windows 统一计划任务入口 |
| 检查频道数据模块 | `11.只读检查_频道数据.bat` | 不写业务数据 |
| 手动同步频道数据 | `12.运行一次_频道数据.bat` | 会真实读取并写入数据 |
| Git 更新后升级验收 | `14.运行电脑升级并验收.bat` | 升级数据库并检查全部配置 |
| 诊断自动任务 | `16.诊断定时任务.bat` | 检查计划任务、进程、数据库和日志 |

也可以在 PowerShell 的项目根目录运行，例如：

```powershell
& ".\01_启动脚本\Windows\14.运行电脑升级并验收.bat"
& ".\01_启动脚本\Windows\10.自动定时任务管理.bat"
```

Windows 作为正式运行端时，建议顺序是：先运行 `14` 升级验收，再分别运行 `6` 和 `12`
真实测试，最后运行 `10` 安装或恢复统一计划任务。Windows 仅作为 VPS 的备用端时，任务必须
保持暂停，不需要删除。

### VPS 首次一键部署

推荐使用 Debian 12 或 Ubuntu 24.04，项目默认安装到
`/opt/YouTube-Feishu-Dashboard`。第一次登录 VPS 后运行：

```bash
curl -fsSL https://raw.githubusercontent.com/kjxv/YouTube-Feishu-Dashboard/main/deploy-vps.sh | bash
```

第一次执行会安装系统依赖、下载项目并创建 Python 环境；发现生产文件尚未上传时会安全停止。
如果 `github.com:443` 无法连接，脚本会自动改用 GitHub 官方源码包。

接下来先在 Windows 运行 `10.自动定时任务管理.bat` 暂停旧任务，并确认当前没有任务正在
执行。再使用 WinSCP/SFTP 上传：

```text
.env                                             → /opt/YouTube-Feishu-Dashboard/.env
secrets/youtube_client_secret.json               → /opt/YouTube-Feishu-Dashboard/secrets/youtube_client_secret.json
secrets/youtube_token.json                       → /opt/YouTube-Feishu-Dashboard/secrets/youtube_token.json
data/yfd.db                                      → /opt/YouTube-Feishu-Dashboard/data/yfd.db
```

如果 VPS 可以直接访问 YouTube，应在 VPS 的 `.env` 中清空 Windows 本机代理：

```env
YFD_HTTPS_PROXY=
```

上传完成后执行：

```bash
cd /opt/YouTube-Feishu-Dashboard
YFD_WINDOWS_PAUSED=YES bash deploy-vps.sh finish
```

该命令会自动备份数据库、升级结构、在线验收、分别执行一次两个真实任务，并安装统一的
`yfd-tick.timer`。任一步骤失败都会停止，不会带着错误启用定时任务。

### VPS 部署验收

```bash
cd /opt/YouTube-Feishu-Dashboard
bash 01_启动脚本/Linux_VPS/自动任务管理.sh status
```

部署完成必须同时满足：

1. `yfd-tick.timer` 显示 `active (waiting)`，并能看到下一次触发时间。
2. 上一次 `yfd-tick.service` 显示 `status=0/SUCCESS`。
3. 飞书“系统运行状态”显示最近状态为“正常”，唤醒时间与完成时间均已更新。
4. Windows 生产计划任务保持暂停。

`yfd-tick.service` 是一次性任务，执行结束后显示 `inactive (dead)` 属于正常状态。

### VPS 常用管理命令

```bash
cd /opt/YouTube-Feishu-Dashboard

# 查看计时器、服务状态及下一次唤醒时间
bash 01_启动脚本/Linux_VPS/自动任务管理.sh status

# 立即执行一次到期检查；不代表所有业务模块都必须采集
bash 01_启动脚本/Linux_VPS/自动任务管理.sh run-now

# 暂停、恢复统一定时任务
bash 01_启动脚本/Linux_VPS/自动任务管理.sh pause
bash 01_启动脚本/Linux_VPS/自动任务管理.sh resume

# 查看最近 200 行日志或持续观察日志
journalctl -u yfd-tick.service -n 200 --no-pager
journalctl -u yfd-tick.service -f

# 分别手动执行一次真实业务任务
bash 01_启动脚本/Linux_VPS/运行一次_最新视频.sh
bash 01_启动脚本/Linux_VPS/运行一次_频道数据.sh

# 备份 SQLite 数据库并执行完整性检查
bash 01_启动脚本/Linux_VPS/备份SQLite数据库.sh
```

### VPS 从 GitHub 一键更新

代码已经推送到 GitHub 后，在 VPS 运行：

```bash
curl -fsSL https://raw.githubusercontent.com/kjxv/YouTube-Feishu-Dashboard/main/deploy-vps.sh | bash
```

脚本会暂停 VPS 定时器、备份数据库、下载代码、升级验收、真实测试并重新启用定时器。如果
检测到一轮任务仍在运行，它会先阻止后续触发并安全停止；等待本轮结束后再次执行相同命令。

### 从 VPS 回退到 Windows

1. 在 VPS 暂停任务：

   ```bash
   cd /opt/YouTube-Feishu-Dashboard
   bash 01_启动脚本/Linux_VPS/自动任务管理.sh pause
   ```

2. 确认 VPS 没有正在执行的 service。
3. 将 VPS 最新的 `data/yfd.db` 和 `secrets/youtube_token.json` 安全覆盖回 Windows。
4. Windows 运行 `14.运行电脑升级并验收.bat`，再运行 `10.自动定时任务管理.bat` 恢复任务。
5. 确认飞书状态更新来自 Windows 后，VPS 继续保持暂停。

更完整的迁移、排错和权限说明见 [Linux VPS 部署说明](00_项目文档/VPS部署说明.md)。

## 安全原则

`.env`、`secrets/`、OAuth 客户端文件、Token、数据库和日志均被 Git 忽略。仓库只保存示例配置、代码、文档、迁移和无密钥的能力目录。提交前请运行 `git status --short` 检查待提交文件。
