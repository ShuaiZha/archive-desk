# Archive Desk backend

第一轮后端是一个只监听本机回环地址的 FastAPI 服务，提供：

- Telegram API ID/API Hash 配置（只保存在后端数据目录）；
- 手机号、验证码及二步验证登录；
- 账号和会话列表；
- 单会话消息 JSON 导出；
- 图片、普通视频和普通文件下载、`.part` 断点恢复、SHA-256 与 Manifest；
- 批量扫描、持久化消息游标、下载前容量检查、分页下载和流式 JSON 产物；
- 按媒体类型/年月分片目录，避免超大单目录和发布阶段整目录复制；
- 持久化任务、任务历史、暂停、继续、取消、重试和安全清理。

## 开发运行

```powershell
cd backend
uv sync --extra test
uv run archivedesk-backend
```

默认地址为 `http://127.0.0.1:8000`。服务故意不监听局域网地址。可用环境变量：

- `ARCHIVEDESK_DATA_DIR`：覆盖后端数据目录；
- `ARCHIVEDESK_HOST`：仅允许 `127.0.0.1`、`localhost` 或 `::1`；
- `ARCHIVEDESK_PORT`：开发端口，默认 `8000`。

Windows 默认数据目录为 `%LOCALAPPDATA%\ArchiveDesk`，包括 SQLite 数据库、加密凭据和 Telethon Session。不要复制或分享 `sessions` 目录；其中的授权密钥等同于已登录账号。

## 第一轮边界

当前下载图片、普通视频，以及没有被识别为视频消息、语音、音频、GIF 或贴纸的普通文档。任务选择一个会话，可自动读取最早和最晚消息时间，并支持自定义日期范围和单文件大小上限。Telegram Secret Chat、完整 Takeout split ranges、多会话批量导出和安装器属于后续阶段。

## 测试

测试使用内存 Fake Telegram provider，不连接 Telegram，也不需要真实 API 凭据：

```powershell
uv run pytest
```
