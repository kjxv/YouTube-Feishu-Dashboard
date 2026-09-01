# Linux VPS 部署说明

## 原则

VPS 与 Windows 使用完全相同的包、CLI、字段目录、数据库模型和任务入口，只更换环境变量、数据库 URL 与操作系统触发器。

## 建议拓扑

- Python 3.11+ 虚拟环境或 Docker 容器
- PostgreSQL（正式环境推荐）
- systemd service + timer 或 cron，每 5～10 分钟执行 `yfd scheduler tick`
- `.env` 和 Google OAuth 文件由服务器权限保护，不进入镜像和 Git
- 日志输出到标准输出或指定日志目录，交由 journald/容器平台轮转

## 上线顺序

1. 拉取仓库并检出明确版本。
2. 运行 Linux 首次配置脚本并填写服务器 `.env`。
3. 执行 `yfd db upgrade`。
4. 将已有 YouTube refresh token 安全迁移到 `secrets/`，或在可交互环境重新授权。
5. 执行 `yfd doctor` 和一次 `--dry-run`。
6. 暂停 Windows 触发器，再启用 VPS timer，避免两个节点同时对同一账号重复同步。

`05_部署与运维/systemd` 提供 service/timer 示例。复制前必须替换其中的安装目录和运行用户；timer 使用 `Persistent=true`，VPS 重启后会尽快触发一次，数据库仍只运行真实到期任务。

多节点同时运行时应使用 PostgreSQL；数据库任务锁提供并发互斥，但飞书写入仍应保持幂等业务键。
