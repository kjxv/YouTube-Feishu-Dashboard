# 05 部署与运维

这个目录只保存运行环境的适配材料，不包含业务逻辑。

- `docker/`：可选的容器镜像与 PostgreSQL Compose 示例。
- `systemd/`：Linux/VPS 定时触发 `scheduler tick` 的服务与计时器模板。
- `windows/`：Windows 整点执行器与计划任务安装、状态、暂停、恢复、卸载工具。

Windows 计划任务、Linux systemd 或 Docker 最终都只触发同一个 Python 调度入口。未来迁移 VPS 时不需要重写业务模块。

Windows 用户无需手动拼接计划任务参数。完成首次配置后，双击
`01_启动脚本/Windows/10.自动定时任务管理.bat`，选择“安装或更新”即可。
