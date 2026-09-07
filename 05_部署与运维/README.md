# 05 部署与运维

这个目录只保存运行环境的适配材料，不包含业务逻辑。

- `docker/`：可选的容器与 PostgreSQL 示例，当前尚未作为正式生产路径验收。
- `systemd/`：Linux/VPS 定时触发 `scheduler tick` 的服务与计时器参考模板。
- `windows/`：Windows 整点执行器与计划任务安装、状态、暂停、恢复、卸载工具。

Windows 计划任务、Linux systemd 或 Docker 最终都只触发同一个 Python 调度入口。未来迁移 VPS 时不需要重写业务模块。

Windows 用户无需手动拼接计划任务参数。完成首次配置后，双击
`01_启动脚本/Windows/10.自动定时任务管理.bat`，选择“安装或更新”即可。

Linux VPS 当前推荐单机 SQLite + systemd。使用
`01_启动脚本/Linux_VPS/升级并验收.sh` 完成部署验收，使用
`01_启动脚本/Linux_VPS/自动任务管理.sh` 安装、查看、暂停或恢复统一 timer，使用
`01_启动脚本/Linux_VPS/备份SQLite数据库.sh` 做一致性备份。完整顺序见
`00_项目文档/VPS部署说明.md`。
