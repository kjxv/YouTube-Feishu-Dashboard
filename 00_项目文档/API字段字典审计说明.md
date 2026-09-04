# API 字段字典审计说明

## 审计结论

原目录的 51 条记录没有发生同步遗漏，但目录本身只覆盖了第一阶段的核心字段，确实不足以作为后续模块的公共能力目录。本次升级为 `2026.09.v2`，新增 78 条，经在线同步后共有 129 条：

| API 类型 | 升级后字段数 |
|---|---:|
| YouTube Data API | 49 |
| YouTube Analytics API | 45 |
| YouTube Reporting API | 35 |
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

## 2026-09-04 当前可用状态逐项审计

本次又把129个 API 字段和37个系统字段逐项对照现有程序能力，共166个字段均已落入唯一状态，不再使用“只要登记进字典就默认已经接入”的判断方式。审计结果保存在随程序发布的 `current_availability.v1.json`，加载字典时会强制检查重复、遗漏和未知字段。

| 当前可用状态 | 字段数 | 可以怎么做 |
|---|---:|---|
| 可直接使用 | 81 | 可以建立映射并启用，第9步允许进入后续执行 |
| 底层已实现，待接入主程序 | 82 | 已有 API 客户端或通用读取基础，但当前最新视频模块还不能直接使用 |
| 底层未实现 | 3 | 当前转换器无法可靠处理，必须先补底层能力 |
| 合计 | 166 | 每个内置标准字段恰好归入一类 |

“可直接使用”的81项包括：32个当前通用视频单值 Data API 字段、8个当前已接入的 Analytics 指标、4个 Reach Reporting 字段（包含兼容 ID 与正式 Reporting ID）以及37个系统生成/计算字段。

“底层已实现，待接入主程序”的82项主要包括：14个频道级 Data API 字段、尚未接入当前视频主程序的 Analytics 维度与指标，以及除 Reach 之外尚无专用报表模板和转写器的 Reporting 字段。

“底层未实现”的3项是 `VIDEO_TAGS`、`VIDEO_REGION_ALLOWED`、`VIDEO_REGION_BLOCKED`。它们返回数组，当前普通单值转换器明确不支持，不能仅靠新增映射直接启用。

此状态是针对“当前最新发布视频实时追踪主程序”的可用性，不代表 YouTube 官方有没有该字段。后续完成新模块或新查询模板时，应同时更新审计清单；第9步仍以飞书字典里同步后的“当前可用状态”为最终检查依据。

## 三种 API 的获取时间与数据截止时间

内置目录 `2026.09.v5` 新增12个可直接使用的时间展示字段。Data、Analytics、Reporting 各自保留“数据获取时间”和“数据截止时间”，每一类又分别提供太平洋时间与北京时间。

- Data API 没有官方统计截止日期。它的两个截止时间由本次获取时刻换算，因此中文名称和说明都明确带 `（推定）`，不能当成 YouTube 官方结算日期。
- Analytics API 和 Reporting API 的官方截止值是太平洋自然日。程序保留原始官方日期，并把该日 `23:59:59` 作为同一个统计边界，分别显示为太平洋时间和北京时间。
- Reporting API 只有在当前视频实际命中报表数据行时才登记获取时间和截止时间；仅检查到报表目录、但当前视频尚无数据时，两者保持为空。重复检查且没有下载到新日报时，保留上一次真正获取数据的时间。
- 12个双时区字段使用带 UTC 偏移量的 ISO 8601 文本，例如 `2026-09-04T01:30:15-07:00` 和 `2026-09-04T16:30:15+08:00`。目标飞书列必须使用文本类型；飞书日期字段会按 Base 时区统一显示，无法在两列中同时保留两个不同时区的可见表示。
- 旧字段 `SYSTEM_LAST_SYNCED_AT`、`ANALYTICS_FETCHED_AT`、`ANALYTICS_DATA_THROUGH_DATE`、`REPORTING_FETCHED_AT`、`REPORTING_DATA_THROUGH_DATE` 继续保留，避免已有映射失效。

## 预计广告收入接入规则

`ANALYTICS_EST_AD_REVENUE` 已正式列为当前最新视频主程序“可直接使用”字段，官方指标为 `estimatedAdRevenue`，目标业务列为“预计广告收入（美元）”。

- 该值是所选视频、所选统计日期范围内，由 Google 销售广告带来的预计净收入，不等于包含 YouTube Premium 等非广告来源的账号全部预计收入。
- 程序在包含收益指标时明确向 Analytics API 发送 `currency=USD`，不依赖隐含默认值。
- OAuth 必须具备 `yt-analytics-monetary.readonly`；当前本机 Token 已确认同时具备普通 Analytics 只读与收益只读范围。
- 新增该指标后，如果最近24小时的旧缓存没有此字段，程序不会把旧缓存误认为完整，而是立即刷新 Analytics。刷新失败时会保留旧指标并把预计广告收入留空，不再让整次三表同步因“运行结果缺少已映射字段”失败。
- 收益属于预计值，可能在月末调整；相关定义以 [YouTube Analytics 官方指标说明](https://developers.google.com/youtube/analytics/metrics) 为准。

## 官方核对依据

- [YouTube Data API：Videos 资源](https://developers.google.com/youtube/v3/docs/videos)
- [YouTube Data API：Channels 资源](https://developers.google.com/youtube/v3/docs/channels)
- [YouTube Analytics：Metrics](https://developers.google.com/youtube/analytics/metrics)
- [YouTube Analytics：Dimensions](https://developers.google.com/youtube/analytics/dimensions)
- [YouTube Analytics：Channel Reports](https://developers.google.com/youtube/analytics/channel_reports)
- [YouTube Reporting API：Channel Reports](https://developers.google.com/youtube/reporting/v1/reports/channel_reports)

## 历史在线验证与本次本地状态

- 本地内置字典当前共166条：129个 API 字段和37个系统字段。飞书线上仍是上一步的154条；本步骤没有连接飞书，新增12个时间字段和状态变更尚未同步到线上。
- API 目录版本：`2026.09.v2`；合并系统字段后的内置目录版本：`2026.09.v5`。
- 标准字段 ID 重复数：0。
- 第二次关键字段标记：0 个新增修改，18 个说明保持不变。
- 在线 Doctor：YouTube Token、数据库迁移、飞书 Base 连接全部通过。
