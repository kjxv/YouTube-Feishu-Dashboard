# Security Policy

## 不要公开的内容

提交代码、创建 Issue 或分享日志前，请移除：

- `data/config.json`
- `data/client_secret.json`
- `data/token.json`
- `.env` 和 `归档/` 中的旧凭据
- Lark App Secret、Base Token、Table ID
- Google OAuth client secret、access token、refresh token
- 包含完整 Base 地址、后台账号或凭据的截图

仓库中的 `.gitignore` 是第一层保护，不应代替提交前的人工检查。

## 如果凭据已经泄露

1. 立即停止公开分享或把仓库暂时设为私有；
2. 在 Lark/飞书开放平台重置 App Secret，并更新本地配置；
3. 如果 Google OAuth 客户端泄露，创建新的桌面 OAuth 客户端并替换 `data/client_secret.json`；
4. 在 Google 账号的第三方访问权限页面撤销旧授权，再运行 `auth_youtube.bat` 生成新 token；
5. 清理 Git 历史中的敏感文件。只在最新提交里删除文件不能清除旧历史；
6. 重新运行 `doctor.bat`、`run_current.bat` 和 `run_daily.bat`。

不要在公开 Issue 中提交真实凭据来寻求帮助。可以提供已脱敏的错误文字和 Doctor 的通过/失败项目。
