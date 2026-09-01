# 03 公共核心框架

这个目录就是所有业务板块共享的“通用框架”。功能模块通过稳定接口调用这里的能力，不自行复制实现。

## 包含的公共能力

- `api/`：YouTube Data、Analytics、Reporting API，OAuth，以及飞书 HTTP 和配置中心。
- `catalog/`：API 字段能力目录和请求规划。
- `core/`：配置、路径、日志、时间和统一错误。
- `db/`：唯一主数据库、模型、Repository/Storage 接口和实现。
- `scheduler/`：统一调度入口、任务锁、失败重试、心跳和断档恢复。
- `services/`：飞书幂等写入、健康状态、字段同步和公共归档服务。
- `db/alembic_scripts/`：随公共包发布的 SQLite/PostgreSQL 数据库迁移历史。

## 边界

业务模块不得直接管理 OAuth、Token、数据库连接、飞书 HTTP、日志输出或系统计划任务。新增公共能力时，应优先在这里提供 Protocol、Repository 或 Service，再由模块注入使用。

内部 Python 包名保持为 `youtube_feishu_dashboard`，避免以后迁往 Linux/VPS 时因中文路径或重命名影响导入接口。
