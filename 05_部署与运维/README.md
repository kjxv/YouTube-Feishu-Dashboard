# 05 部署与运维

这个目录只保存运行环境的适配材料，不包含业务逻辑。

- `docker/`：可选的容器镜像与 PostgreSQL Compose 示例。
- `systemd/`：Linux/VPS 定时触发 `scheduler tick` 的服务与计时器模板。

Windows 计划任务、Linux systemd 或 Docker 最终都只触发同一个 Python 调度入口。未来迁移 VPS 时不需要重写业务模块。
