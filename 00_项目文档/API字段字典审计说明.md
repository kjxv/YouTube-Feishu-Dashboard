# API 字段字典审计说明

## 审计结论

原目录的 51 条记录没有发生同步遗漏，但目录本身只覆盖了第一阶段的核心字段，确实不足以作为后续模块的公共能力目录。本次升级为 `2026.09.v2`，新增 78 条，经在线同步后共有 129 条：

| API 类型 | 升级后字段数 |
|---|---:|
| YouTube Data API | 49 |
| YouTube Analytics API | 47 |
| YouTube Reporting API | 33 |
| 合计 | 129 |

本次补充重点包括：频道与视频元数据、缩略图、地区限制、状态、直播计划与同时观看人数、流量来源、播放位置、设备和操作系统、受众年龄与性别、订阅状态、YouTube Premium、独立观众、广告收益、CPM，以及 Reporting 批量报表的对应列。

## 为什么业务表有 99 列，原字典却只有 51 条

三张业务表共有 45 + 35 + 19 = 99 列，但“业务表列数”不能与“API字段数”直接比较：

1. 同一个 API 字段会在主表、快照表和对比表重复使用，例如播放量、观看时长、平均观看百分比。
2. 很多列由系统生成，例如视频唯一编号、快照唯一编号、数据抓取时间、时间档、追踪状态。
3. 很多列由系统计算，例如本周期新增播放量、每小时播放速度、净增订阅、千次播放订阅、各时间里程碑。
4. 有些列是飞书公式、关联或查找字段，例如关联视频、当前追踪视频、平均观看时长显示。
5. 视频链接和部分展示字段可以由视频 ID 组合生成，不需要额外调用 YouTube 指标接口。

因此，非 API 字段应继续登记在“模块字段需求与映射”中，并明确标注为“系统生成”“系统计算”或“飞书公式”，不能为了凑数量伪装成 YouTube 官方字段。

## 本次目录边界

V2 的目标是“创作者数据中心可实际使用的分析型能力目录”，不是把 YouTube 所有管理型、上传处理、版权资产和内容所有者专属字段一次性塞入飞书。以下内容暂不纳入：

- 已弃用且固定返回无意义值的字段；
- 上传文件编码、转码进度、处理建议等管理型字段；
- CMS 内容所有者、版权声明、资产和音乐结算专属的大批字段；
- 当前模块没有分析价值的播放器 HTML、局部本地化资源等字段。

以后新增频道历史、流量来源、收益或全视频模块时，应先从本目录选字段；官方增加或变更字段时，以增量 JSON 发布新目录版本，再同步到飞书，不直接覆盖旧版本文件。

## 官方核对依据

- [YouTube Data API：Videos 资源](https://developers.google.com/youtube/v3/docs/videos)
- [YouTube Data API：Channels 资源](https://developers.google.com/youtube/v3/docs/channels)
- [YouTube Analytics：Metrics](https://developers.google.com/youtube/analytics/metrics)
- [YouTube Analytics：Dimensions](https://developers.google.com/youtube/analytics/dimensions)
- [YouTube Analytics：Channel Reports](https://developers.google.com/youtube/analytics/channel_reports)
- [YouTube Reporting API：Channel Reports](https://developers.google.com/youtube/reporting/v1/reports/channel_reports)

## 在线验证结果

- 飞书“API字段字典”记录数：129。
- 本地目录版本：`2026.09.v2`。
- 标准字段 ID 重复数：0。
- 第二次关键字段标记：0 个新增修改，18 个说明保持不变。
- 在线 Doctor：YouTube Token、数据库迁移、飞书 Base 连接全部通过。
