# YouTube → Lark Dashboard V2

把 YouTube 频道的当前数据和 YouTube Analytics 日数据，自动同步到 Lark/飞书多维表格（Base）。项目面向 Windows 10/11，可整体迁移到另一台电脑；配置、OAuth 文件、日志与源码分离，不需要在 Python 代码中填写账号信息。

> **安全提醒：** 不要把真实的 `data/config.json`、`data/client_secret.json`、`data/token.json`、`.env`、`logs` 或 `归档` 上传到 GitHub。仓库中的 `.gitignore` 已默认排除这些内容，但首次提交前仍必须人工检查一次。

## 功能概览

- `current`：同步频道当前快照和全部视频的当前播放量、点赞数、评论数等数据。
- `daily`：回查最近若干天的 YouTube Analytics，按“视频 × 日期”和“频道 × 日期”更新历史数据。
- `backfill`：一次性从频道创建日开始回填全部视频历史；按视频发布时间过滤，支持分段、检查点续跑和错误记录清理。
- `latest`：自动识别最新发布的视频，每 5 分钟保存播放量、点赞数、评论数及各项增量，默认持续追踪 168 小时。
- 使用唯一键更新已有记录，重复运行不会为同一视频或同一天重复新增记录。
- `setup.bat` 交互式生成配置，不需要修改源码。
- `doctor.bat` 一次检查 Python、依赖、配置、代理、Lark 数据表和 YouTube API。
- `install.bat` 自动寻找或安装 Python、创建项目专用 `.venv` 并安装依赖。
- `install_tasks.bat` 创建 Windows 定时任务，项目路径不写死。
- 同步过程同时显示在窗口并写入 `logs/current.log`、`logs/daily.log` 或 `logs/latest.log`。

数据流如下：

```text
YouTube Data API ──────> 视频主表 + 当天频道快照 + 最新视频实时追踪表
YouTube Analytics API ─> 视频历史表 + 频道历史表
                              │
                              └─ Lark/飞书多维表格 Base
```

## 已验证状态

V2 已在 Windows 环境完成实际 smoke test：

- Doctor：所有检查通过；
- YouTube Data API 和 YouTube Analytics API 均可访问；
- Lark App 与四张 Base 表均可访问；
- `current` 正常更新 141 个视频；
- `daily` 正常更新 705 条视频历史和 5 条频道历史；
- 全历史视频数据已回填至 19,882 条，发布前无效记录为 0；
- 最新视频实时追踪表已自动创建，并通过新增与同时间档更新测试；
- current、daily 和 latest 同步任务最终退出码均为 `0`。

不同账号、权限、网络和表结构仍需在各自电脑上通过 Doctor 验证。

## 部署前需要准备什么

1. 一个拥有目标 YouTube 频道访问权的 Google 账号；
2. 一个 Google Cloud 项目；
3. 一个 Lark/飞书企业自建应用；
4. 一个包含三张基础数据表的多维表格 Base；实时追踪表由程序自动创建；
5. Windows 10 或 Windows 11；
6. 能访问 Google API 的网络。需要代理时，提前确认本地 HTTP 代理地址和端口。

Python 需要 3.9 或更高版本。通常直接运行 `install.bat` 即可，安装器会优先使用已有 Python，否则尝试自动安装 Python 3.11。

---

## 第一部分：开启 YouTube API

### 1. 创建 Google Cloud 项目

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)；
2. 登录管理 YouTube 频道的 Google 账号；
3. 在顶部项目选择器中创建一个新项目，例如 `YouTube-Lark-Dashboard`；
4. 切换到刚创建的项目。

### 2. 启用两个 API

进入“API 和服务”→“库”，分别搜索并启用：

- **YouTube Data API v3**
- **YouTube Analytics API**

本项目不依赖 YouTube Reporting API。Google 的 Data API 入门说明见 [YouTube Data API Overview](https://developers.google.com/youtube/v3/getting-started)。

### 3. 配置 Google Auth Platform

在 Google Cloud Console 打开“Google Auth Platform”，依次完成：

1. **Branding（品牌）**：填写应用名称、用户支持邮箱和开发者联系邮箱；
2. **Audience（受众）**：
   - Google Workspace 组织内部使用，可在符合条件时选择 `Internal`；
   - 普通个人 Google/YouTube 账号通常选择 `External`；
3. 如果应用处于 `Testing`，把实际授权的 Google 账号加入 **Test users（测试用户）**；
4. **Data Access（数据访问）**：添加本项目需要的三个 OAuth scope：

```text
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/yt-analytics.readonly
https://www.googleapis.com/auth/yt-analytics-monetary.readonly
```

前两个用于读取频道、视频和 Analytics 数据；第三个用于读取 `estimatedRevenue` 预估收入。官方 scope 定义见 [OAuth 2.0 Scopes for Google APIs](https://developers.google.com/identity/protocols/oauth2/scopes#youtube)。

> **7 天失效提醒：** External 应用如果一直处于 `Testing`，授权通常会在 7 天后失效，本项目的定时任务也会随之停止。长期自动运行前，应根据账号类型把应用配置为适合的发布状态，并按 Google 当前政策完成可能需要的验证。详见 [Google OAuth 2.0](https://developers.google.com/identity/protocols/oauth2) 和 [OAuth App Verification](https://support.google.com/cloud/answer/13463073)。

### 4. 创建桌面 OAuth 客户端

1. 在 Google Auth Platform 打开 **Clients（客户端）**；
2. 点击“Create client（创建客户端）”；
3. 应用类型选择 **Desktop app（桌面应用）**；
4. 创建后下载 JSON 文件；
5. 把文件重命名为 `client_secret.json`；
6. 放到项目的 `data` 文件夹：

```text
YouTube-Feishu-Dashboard\data\client_secret.json
```

这个 JSON 是 OAuth 客户端凭据，不能上传到公开仓库，也不要发到公开聊天或 Issue。桌面应用授权流程可参考 [OAuth 2.0 for installed applications](https://developers.google.com/youtube/reporting/guides/authorization/installed-apps)。

---

## 第二部分：创建 Lark/飞书应用和 Base

### 1. 创建企业自建应用

根据所用版本进入：

- Lark：[Lark Developer Console](https://open.larksuite.com/app)
- 飞书：[飞书开放平台](https://open.feishu.cn/app)

创建一个企业自建应用，然后在“凭证与基础信息”中记录：

```text
App ID
App Secret
```

给应用申请多维表格读写所需权限。开放平台中通常对应多维表格应用权限 `bitable:app`；权限名称可能随控制台语言和版本调整，以“查看、评论、编辑和管理多维表格”为准。完成权限配置后发布应用，并由企业管理员审批。

最后把该自建应用添加为目标 Base 的文档应用/协作者，并授予可编辑和管理数据表权限。只在开放平台申请 API 权限、但没有给具体 Base 授权，Doctor 仍会显示数据表不可访问，latest 也无法自动建表。

飞书官方示例可参考 [多维表格 OpenAPI 使用说明](https://open.feishu.cn/community/articles/7310068607639584771?lang=zh-CN)。

### 2. 创建一个 Base 和三张基础表

在同一个 Base 中创建：

1. 视频主表
2. 视频历史数据表
3. 频道历史数据表

字段名称必须与下面完全一致，包括空格、大小写、中文括号和货币单位。可以增加自己的字段或视图，但不要重命名脚本使用的字段。

第四张 `最新视频实时追踪` 表不需要手工创建。首次运行 `run_latest.bat` 时，程序会在同一个 Base 中自动建表和创建字段，并把 Table ID 保存到本机配置。

#### 视频主表

| 字段名 | 建议字段类型 | 说明 |
|---|---|---|
| `Video ID` | 单行文本 | 唯一键 |
| `视频标题` | 单行文本 | 视频标题 |
| `视频类型` | 单选 | `短视频`、`长视频`、`直播` 或 `未知` |
| `发布时间` | 日期 | YouTube 发布时间 |
| `视频链接` | 超链接 | 视频地址 |
| `缩略图URL` | 超链接 | 缩略图地址 |
| `当前播放量` | 数字 | 当前累计播放量 |
| `点赞数` | 数字 | 当前点赞数 |
| `评论数` | 数字 | 当前评论数 |
| `视频时长` | 单行文本 | 格式化后的视频时长 |
| `最后同步时间` | 日期 | 最后一次写入时间 |

#### 视频历史数据表

| 字段名 | 建议字段类型 | 说明 |
|---|---|---|
| `唯一键` | 单行文本 | `Video ID_日期`，防止重复 |
| `日期` | 日期 | Analytics 数据日期 |
| `Video ID` | 单行文本 | YouTube Video ID |
| `视频标题` | 单行文本 | 视频标题 |
| `视频类型` | 单选 | 用于同类型视频之间的筛选、分组和对比 |
| `发布时间` | 日期 | YouTube 发布时间 |
| `发布后天数` | 数字 | 程序按 YouTube Analytics 太平洋时区计算，避免跨时区出现负数 |
| `当日播放量` | 数字 | 当日 views |
| `观看时长（分钟）` | 数字 | 当日观看分钟数 |
| `平均观看时长（秒）` | 数字 | 平均观看秒数 |
| `平均观看百分比` | 数字 | API 返回的百分比数值 |
| `新增订阅` | 数字 | 当日新增订阅 |
| `取消订阅` | 数字 | 当日取消订阅 |
| `预估收入（USD）` | 数字 | 当日预估收入 |
| `最后同步时间` | 日期 | 最后一次写入时间 |

#### 最新视频实时追踪表（自动创建）

程序会自动创建 `唯一键`、`采集时间`、`Video ID`、`视频标题`、`视频类型`、`发布时间`、`发布后分钟数`、当前播放/点赞/评论数、三项增量、`视频链接` 和 `最后同步时间` 共 15 个字段。每个时间档使用唯一键写入；同一档重复执行只更新，不重复新增。

`视频类型` 优先读取 YouTube Analytics 官方的 `creatorContentType`。Analytics 尚未结算的新视频会暂时根据 YouTube Data API 返回的原始宽高、时长和直播信息判断，后续 `daily` 会用官方分类覆盖。程序不会只用“少于几分钟”这一条规则猜测 Shorts。

#### 频道历史数据表

| 字段名 | 建议字段类型 | 说明 |
|---|---|---|
| `日期Key` | 单行文本 | 日期唯一键，格式 `YYYY-MM-DD` |
| `日期` | 日期 | 数据日期 |
| `订阅人数` | 数字 | 当前频道订阅快照 |
| `频道总播放量` | 数字 | 当前频道总播放快照 |
| `视频数量` | 数字 | 当前公开视频数量 |
| `订阅快照变化` | 数字 | 与前一份频道快照的差值 |
| `当日播放量` | 数字 | 当日 Analytics views |
| `观看时长（分钟）` | 数字 | 当日频道观看分钟数 |
| `新增订阅` | 数字 | 当日新增订阅 |
| `取消订阅` | 数字 | 当日取消订阅 |
| `预估收入（USD）` | 数字 | 当日预估收入 |
| `最后同步时间` | 日期 | 最后一次写入时间 |

### 3. 获取 Base Token 和三个 Table ID

打开任意一张表，浏览器地址通常包含：

```text
https://example.larksuite.com/base/BASE_TOKEN?table=TABLE_ID&view=VIEW_ID
```

- `/base/` 后、问号前的部分是 `Base Token`；
- `table=` 后的值是当前表的 `Table ID`；
- `view=` 后的 View ID 本项目不需要。

依次打开三张表，记录三个不同的 Table ID。不要在公开截图、README 或 GitHub Issue 中暴露真实 Base 地址。

---

## 第三部分：下载和首次安装

### 1. 获取项目

可以在 GitHub 页面点击 **Code → Download ZIP**，解压到任意固定目录；也可以使用：

```powershell
git clone <你的仓库地址>
```

建议目录路径简短且长期不移动，例如：

```text
C:\YouTube-Feishu-Dashboard
```

不要复制其他电脑的 `.venv`。它是当前电脑专用的 Python 环境，新电脑会自动重新创建。

### 2. 放入 OAuth 客户端文件

确认项目中存在 `data` 文件夹，然后把 Google Cloud 下载的文件放到：

```text
data\client_secret.json
```

公开仓库只保留空的 `data/.gitkeep`；真实文件需要每位使用者自己放入。

### 3. 运行安装器

双击：

```text
install.bat
```

安装器会依次：

1. 检查 Python 3.9+；
2. 没有 Python 时优先通过 WinGet 安装 Python 3.11；
3. WinGet 不可用时，从 python.org 下载并验证 Python Software Foundation 签名后安装；
4. 创建项目自己的 `.venv`；
5. 安装 `requirements.txt` 中的依赖；
6. 启动 `setup.py` 进入首次配置。

公司电脑若禁止 WinGet、下载安装程序或访问 python.org，需要管理员先手工安装 [Python 3.11.9](https://www.python.org/downloads/release/python-3119/)，然后重新双击 `install.bat`。

### 4. 按提示填写配置

首次设置会依次询问：

```text
Lark App ID
Lark App Secret
Lark Base Token
视频主表 Table ID
视频历史表 Table ID
频道历史表 Table ID
实时追踪表 Table ID（首次可留空，由程序自动创建）
OAuth client_secret 路径
OAuth token 路径
是否使用代理
代理地址和端口
Analytics 回查天数
current 更新间隔（分钟）
最新视频追踪间隔（分钟）
每条最新视频持续追踪小时数
daily 每日运行时间（HH:MM）
```

通常保留下面两个默认路径：

```text
data/client_secret.json
data/token.json
```

中国大陆网络环境常需要代理。示例：

```text
enabled：true
host：127.0.0.1
port：10808
```

端口必须与当前电脑代理软件实际开放的 **HTTP 代理端口** 一致；不需要代理时选择 `N`。

### 5. 完成 YouTube OAuth 授权

首次没有 `data/token.json` 时，安装流程会引导授权；也可以双击：

```text
auth_youtube.bat
```

浏览器打开后：

1. 登录拥有目标 YouTube 频道权限的 Google 账号；
2. 如果一个账号管理多个频道，确认授权后 Doctor 显示的是目标频道；
3. 同意三个只读/Analytics 权限；
4. 成功后凭据保存到 `data/token.json`。

`token.json` 含有可续期授权，敏感程度高于普通配置文件。不要通过公开 GitHub 在电脑之间传递它。

---

## 第四部分：检查并开始同步

### 1. 运行 Doctor

双击：

```text
doctor.bat
```

不要使用系统命令 `python doctor.py`，否则可能绕过项目 `.venv`。Doctor 只读取并检查，不会修改 Lark 表格。它会检查：

- Python 版本和依赖；
- `data/config.json` 格式和必填项；
- OAuth 客户端和 token；
- token 是否属于当前 OAuth 客户端；
- 代理端口；
- Lark App 和已配置的数据表；
- YouTube Data API；
- YouTube Analytics API。

理想结果：

```text
检查完成：通过 13，失败 0，提醒 0
环境可用，可以运行 current/daily/latest。
```

还要确认 Doctor 显示的 YouTube 频道名称正是目标频道。

### 2. 手工运行 current

双击：

```text
run_current.bat
```

检查窗口末尾或 `logs/current.log`：

```text
CURRENT 更新完成
END current exit=0
```

然后确认视频主表和当天频道快照已经更新。

### 3. 手工运行 daily

双击：

```text
run_daily.bat
```

`daily` 会逐个查询视频 Analytics，通常比 `current` 慢。检查窗口末尾或 `logs/daily.log`：

```text
DAILY 更新完成
END daily exit=0
```

再确认视频历史表和频道历史表出现对应日期的数据。

### 4. 手工运行 latest

双击 `run_latest.bat`。首次运行会自动创建 `最新视频实时追踪` 表，随后写入最新视频的一条快照。检查 `logs/latest.log` 中出现 `END latest exit=0`。同一个 5 分钟时间档重复运行会更新原记录，不会重复新增。

该模式使用 YouTube Data API 的累计播放量、点赞数和评论数，属于约 5 分钟级近实时追踪。观看时长、订阅和收入仍由 daily 使用 YouTube Analytics API 更新，存在官方结算延迟。

只有 Doctor、current、daily 和 latest 全部通过后，才建议安装定时任务。

### 5. 安装 Windows 定时任务

双击：

```text
install_tasks.bat
```

安装任务需要 Windows 管理员权限。新版脚本会自动弹出 UAC 确认窗口；点击“是”即可，不必手工打开管理员命令行。

默认计划来自 `data/config.json`：

```text
current：每 15 分钟
latest：每 5 分钟
daily：每天 18:00
```

任务名形如：

```text
YouTube-Lark-Current-XXXXXXXX
YouTube-Lark-Daily-XXXXXXXX
YouTube-Lark-Latest-XXXXXXXX
```

最后 8 位根据 Lark App ID 和 Base Token 生成。同一配置重复运行 `install_tasks.bat` 会更新原任务，不会不断创建重复任务。

修改运行时间、移动项目目录或迁移电脑后，需要重新运行 `install_tasks.bat`。定时运行使用静默模式，不会弹出等待按键的窗口；结果仍会写入日志。

### 6. 删除定时任务

按 `Win + R`，输入 `taskschd.msc` 打开“任务计划程序”，在“任务计划程序库”中找到上面三个任务，右键选择“删除”。

也可以在命令提示符中使用安装时显示的完整任务名：

```cmd
schtasks /Delete /TN "YouTube-Lark-Current-XXXXXXXX" /F
schtasks /Delete /TN "YouTube-Lark-Daily-XXXXXXXX" /F
schtasks /Delete /TN "YouTube-Lark-Latest-XXXXXXXX" /F
```

删除任务只会停止自动运行，不会删除项目文件或 Lark 中已有数据。

### 7. 首次建立完整历史数据

首次部署、或视频历史表只有最近一段时间的数据时，双击：

```text
run_backfill.bat
```

它会：

1. 读取 YouTube 频道创建日期和全部视频发布时间；
2. 检查并备份“历史日期早于视频发布时间”的无效记录；
3. 删除这些无效记录，避免“发布后天数”出现负数；
4. 从每个视频的实际发布日期开始，分段查询完整 YouTube Analytics；
5. 通过 `Video ID_日期` 唯一键新增或更新飞书记录；
6. 每完成一个视频保存 `data/backfill_checkpoint.json`，中断后再次双击即可继续；
7. 最后复核视频历史表，确保发布前无效记录为 0，并同步全部记录的 `视频类型`。

这是一次性初始化任务，不应添加到 Windows 定时任务。完成后继续让 `daily` 每天回查最近 7 天即可。YouTube 最近几天的 Analytics 可能尚未结算，后续 daily 会自动补齐。

删除前备份保存在：

```text
data/backups/invalid_video_history_YYYYMMDD_HHMMSS.json
```

---

## 配置文件说明

真实配置保存在 `data/config.json`，示例见 `config.example.json`：

```json
{
  "version": 2,
  "lark": {
    "app_id": "请填写 Lark App ID",
    "app_secret": "请填写 Lark App Secret",
    "base_token": "请填写 Base Token",
    "video_table_id": "请填写视频主表 Table ID",
    "history_table_id": "请填写视频历史表 Table ID",
    "channel_table_id": "请填写频道历史表 Table ID",
    "latest_table_id": ""
  },
  "youtube": {
    "client_secret_file": "data/client_secret.json",
    "token_file": "data/token.json"
  },
  "proxy": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 10808
  },
  "update": {
    "analytics_lookback_days": 7,
    "backfill_chunk_days": 90,
    "current_interval_minutes": 15,
    "latest_interval_minutes": 5,
    "latest_tracking_hours": 168,
    "daily_time": "18:00"
  }
}
```

- `analytics_lookback_days`：daily 每次回查的天数。YouTube Analytics 可能延迟，回查可补齐或修正最近数据；
- `backfill_chunk_days`：全历史回填每次查询的日期段长度，默认 90 天；
- `current_interval_minutes`：Windows current 任务的运行间隔；
- `latest_interval_minutes`：最新视频追踪任务的运行间隔，默认 5 分钟；
- `latest_tracking_hours`：一条视频发布后持续近实时追踪的时长，默认 168 小时（7 天）；
- `daily_time`：daily 任务的本地时间，使用 24 小时制 `HH:MM`；
- `latest_table_id`：首次可留空；运行 latest 后自动创建表并写回。更换 Base Token 时 setup 会自动清空它；也可在 setup 中输入 `AUTO` 重新建表；
- 修改 update 参数后必须重新运行 `install_tasks.bat`，Windows 才会采用新计划。

建议通过 `setup.bat` 修改配置，避免 JSON 格式错误。

## 迁移电脑和更换账号

### 同一账号迁移到新电脑

如果这是你自己的私有部署，可以通过安全方式复制整个项目及 `data` 中三个真实文件，但不要经由公开 GitHub：

1. 新电脑不要沿用旧电脑的 `.venv`；
2. 双击 `install.bat`，为新电脑重建 `.venv`；
3. setup 显示原配置时，直接按回车保留；
4. 双击 `doctor.bat`；
5. 依次测试 `run_current.bat`、`run_daily.bat` 和 `run_latest.bat`；
6. 最后运行 `install_tasks.bat`；
7. 确认新电脑稳定后，删除旧电脑上的三个定时任务，避免两台机器重复请求 API。

### 更换 YouTube 运营账号

1. 双击 `auth_youtube.bat`；
2. 在浏览器中登录新账号并授权；
3. 新 token 会保存到 `data/token.json`；
4. 运行 Doctor，确认显示的新频道名称正确；
5. 再测试 current、daily 和 latest。

如果更换了 Google Cloud OAuth 客户端，先替换 `data/client_secret.json`，再重新授权。旧客户端与新 token 不匹配时 Doctor 会明确提示。

### 更换 Lark App 或 Base

1. 双击 `setup.bat`；
2. 更新 App ID、App Secret、Base Token 和三张基础表的 Table ID；`latest_table_id` 可清空并让程序在新 Base 自动创建；
3. 确保新应用已发布、获批并能编辑目标 Base；
4. 运行 Doctor；
5. 确认目标 Base 无误后再运行同步；
6. 重新运行 `install_tasks.bat`。

在未确认 Base Token 和 Table ID 之前不要运行同步，以免把另一个频道的数据写入错误的 Base。

## 常见问题

### `No module named 'google'`

通常是直接运行了系统 Python，或依赖尚未安装。重新双击 `install.bat`，之后使用 `doctor.bat`，不要使用 `python doctor.py`。

### `PROXY_TYPE_HTTP` / PySocks 错误

项目环境缺少或没有更新代理依赖。最新版 `requirements.txt` 已包含 `PySocks`。重新运行 `install.bat` 补齐依赖。

### Doctor 提示代理端口不可连接

确认代理软件已启动，且 `setup.bat` 中填写的是 HTTP 代理端口。如果电脑可以直接访问 Google API，在 setup 中关闭代理。

### OAuth 页面显示“应用未经验证”或无法登录

确认当前账号已加入 Google Auth Platform 的 Test users；检查三个 scope 和 OAuth 客户端类型；长期运行还要处理 Testing 状态的 7 天授权有效期。不要通过降低浏览器安全设置绕过。

### token 过期、被撤销或换错频道

双击 `auth_youtube.bat` 重新授权，然后运行 Doctor 确认频道名称。

### Lark App 可以获取 token，但表不可访问

检查：

- Base Token 和三个 Table ID 是否抄错；
- 应用是否已发布并获管理员审批；
- 是否申请了多维表格读写权限；
- 是否把应用添加到具体 Base 并授予编辑权限；
- 字段名和字段类型是否与本文一致。

### BAT 窗口一闪而过或定时任务无输出

手工运行会在结束后等待按键；定时任务运行时不会弹出窗口。查看：

```text
logs\current.log
logs\daily.log
logs\latest.log
```

日志末尾 `exit=0` 表示成功，非零表示失败，具体原因通常在它前面的几行。

### 项目移动后定时任务不运行

Windows 任务保存了安装时的项目路径。移动后重新运行 `install_tasks.bat`，让任务指向新位置。

### “视频类型”缺失或需要重新同步

程序会在日常任务中自动维护该字段。如需单独重新识别并回填三张视频表，可运行：

```text
.venv\Scripts\python.exe run_sync.py types
```

该命令只新增或更新 `视频类型`，不会改动播放量、观看时长、订阅或收入等指标。

### “发布后天数”出现负数

新版把 `发布后天数` 改为数字字段，由程序按 YouTube Analytics 使用的美国太平洋时区计算。`daily` 会限制每个视频的查询起点，`run_backfill.bat` 会备份并清除真正早于发布时间的无效记录，因此既保留跨时区的真实首日数据，也不会出现负数。

## 文件用途

| 文件或目录 | 用途 | 是否可上传公开仓库 |
|---|---|---|
| `README.md` | GitHub 完整部署与使用说明 | 是 |
| `使用说明.md` | 本地简明操作手册 | 是 |
| `config.example.json` | 无真实账号信息的配置模板 | 是 |
| `data/.gitkeep` | 让空的 data 目录保留在 Git 中 | 是 |
| `data/config.json` | 本机真实 Lark、代理和更新配置 | **否** |
| `data/client_secret.json` | Google OAuth 客户端凭据 | **否** |
| `data/token.json` | YouTube 账号 OAuth token | **否** |
| `data/video_types.json` | 视频类型缓存及分类来源 | 否 |
| `logs/` | current/daily/latest/types 运行日志 | **否** |
| `归档/` | V1 旧脚本、旧配置和旧凭据 | **否** |
| `.venv/` | 当前电脑专用 Python 环境 | 否 |
| `app_config.py` | 读取、合并和校验 V2 配置 | 是 |
| `youtube_lark.py` | current/daily/backfill/latest/types 同步主程序 | 是 |
| `setup.py` / `setup.bat` | 首次初始化和配置调整 | 是 |
| `auth_youtube.py` / `.bat` | YouTube OAuth 授权或换号 | 是 |
| `doctor.py` / `.bat` | 环境和连接诊断 | 是 |
| `install.ps1` / `install.bat` | 安装 Python、`.venv` 和依赖 | 是 |
| `run_sync.py` | 运行同步并同时写窗口和日志 | 是 |
| `run_current.bat` | 手工或定时执行 current | 是 |
| `run_daily.bat` | 手工或定时执行 daily | 是 |
| `run_latest.bat` | 手工或每 5 分钟执行最新视频追踪 | 是 |
| `run_backfill.bat` | 一次性回填频道创建以来的完整历史，支持续跑 | 是 |
| `install_tasks.py` / `.bat` | 创建或更新 Windows 定时任务 | 是 |
| `requirements.txt` | Python 依赖版本范围 | 是 |

## 上传 GitHub 前的安全检查

这个项目当前目录不会自动变成 Git 仓库。准备发布时，在项目根目录执行：

```powershell
git init
git status --ignored
git add .
git status
```

在 `git commit` 之前，确认暂存区中 **没有**：

```text
data/config.json
data/client_secret.json
data/token.json
.env
logs/*.log
归档/
.venv/
```

只看到 `data/.gitkeep` 和 `logs/.gitkeep` 是正常的。确认无敏感文件后再提交：

```powershell
git commit -m "Initial public release"
git branch -M main
git remote add origin <你的 GitHub 仓库地址>
git push -u origin main
```

注意事项：

- 不要把含真实 Base 地址、App ID、频道后台或 OAuth 信息的截图放进仓库；
- 不要在 Issue 中粘贴完整日志，先删除 token、App Secret、Base Token、Table ID 和本机用户名；
- 如果敏感文件已经提交过，仅在新提交中删除并不够，因为旧 Git 历史仍可下载；应立即轮换相关凭据，并清理 Git 历史后再公开；
- 公开发布前请自行选择开源许可证。没有 `LICENSE` 文件时，默认并不等于他人可以自由复制、修改和分发。

## 推荐日常顺序

新装、迁移、换账号或长时间未使用后，统一按下面顺序：

```text
install.bat 或 setup.bat
        ↓
doctor.bat
        ↓
run_current.bat
        ↓
run_backfill.bat（首次或历史缺失时运行一次）
        ↓
run_daily.bat
        ↓
run_latest.bat
        ↓
install_tasks.bat
```

只要 Doctor 全部通过、频道名称正确，并且三个日常任务日志最后都是 `exit=0`，就可以让 Windows 定时任务长期自动运行。

## 官方参考资料

- [YouTube Data API v3 — Getting Started](https://developers.google.com/youtube/v3/getting-started)
- [YouTube Analytics API](https://developers.google.com/youtube/analytics)
- [Google OAuth 2.0 Scopes](https://developers.google.com/identity/protocols/oauth2/scopes#youtube)
- [OAuth 2.0 for installed applications](https://developers.google.com/youtube/reporting/guides/authorization/installed-apps)
- [Google Auth Platform](https://support.google.com/cloud/answer/15544987)
- [OAuth App Verification](https://support.google.com/cloud/answer/13463073)
- [Lark Developer Platform](https://open.larksuite.com/)
- [飞书开放平台](https://open.feishu.cn/)
