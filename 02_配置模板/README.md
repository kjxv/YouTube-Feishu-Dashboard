# 配置模板

这里保存可以提交到 Git 的非敏感配置模板，目前主要是飞书配置中心公共表结构。首次配置时推荐运行 `yfd feishu bootstrap-config`，无需手工创建四张公共表；当前结构以 `飞书配置中心表结构.v2.json` 为准，V1 文件只保留历史参考。

真实 App Secret、OAuth 客户端文件和 Token 不放在这里，分别写入根目录 `.env` 和 `secrets/`；这些路径已被 Git 忽略。
