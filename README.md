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

## 安全原则

`.env`、`secrets/`、OAuth 客户端文件、Token、数据库和日志均被 Git 忽略。仓库只保存示例配置、代码、文档、迁移和无密钥的能力目录。提交前请运行 `git status --short` 检查待提交文件。
