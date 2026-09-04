# 最新发布视频实时追踪

- Python 包：`yfd_latest_video_tracker`
- 调度任务 ID：`latest-video-tracker`
- 状态：第一阶段 V1 已实现

该模块读取“账号非敏感配置”中的 `tracking_video_ids`，可同时追踪 1～10 个属于当前授权频道的视频。在发布后默认 7 天内按真实抓取时间保存快照，计算每个视频相邻真实快照的增量，并通过公共飞书记录服务进行幂等写入。电脑离线时不会补造缺失的数据点。

## 第一次运行前的安全检查

先运行只读预览：

```powershell
.\.venv\Scripts\yfd.exe modules preview latest_video_tracker
```

该命令会读取手动列表中的视频，逐条显示可访问性、期限状态、标准字段和预计写入，但不会写入飞书或业务数据库。OAuth
访问令牌过期时，公共授权组件可能在本地刷新 `secrets/youtube_token.json`。

再运行三表同步前检查：

```powershell
.\.venv\Scripts\yfd.exe modules validate-sync latest_video_tracker
```

它读取飞书配置中心和三张业务表的字段结构，同时检查 `tracking_video_ids` 的格式、数量和重复项。报告会显示视频列表检查结果、Table ID、远端可访问性、启用映射数、缺失列以及三表写入实现状态。它不会请求 YouTube；视频是否存在、能否访问及是否属于当前频道由只读预览或正式同步确认。

代码职责：

- `manifest.py`：模块身份、所需 API 字段和默认飞书字段映射。
- `service.py`：最新视频追踪业务流程和数据转换。
- `preview.py`：只访问 YouTube 的安全预览，不依赖数据库或飞书。
- `task.py`：接入公共调度器的薄适配层。

OAuth、YouTube/飞书客户端、数据库连接、归档、锁和重试均来自 `03_公共核心框架`。
