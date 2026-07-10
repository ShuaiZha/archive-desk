# 当前实施状态

更新时间：2026-07-10

Archive Desk 当前版本为 **0.1.0 Alpha**，实现以单机、单用户、单账号活动任务为边界，核心目标是可靠导出一个 Telegram 云端会话中的消息、图片、普通视频和普通文件。

## 已落地

- Telegram API 凭据加密保存，手机号、验证码和可选 2FA 登录。
- 登录流程查询、验证码重发和取消，取消后清理临时 Session。
- 会话刷新、搜索、游标分页和消息时间边界查询。
- 创建任务使用 `Idempotency-Key`，相同键和相同配置返回原任务，不同配置返回 `IDEMPOTENCY_CONFLICT`。
- 日期范围按显式 IANA `time_zone` 解释，Windows 通过 `tzdata` 提供时区数据库。
- 扫描开始时持久化 `upper_message_id`，恢复时不会纳入快照之后的新消息。
- 消息每 250 条批量提交，媒体每 500 条分页处理。
- 图片、普通视频、普通文件下载，支持单文件限制和无限制。
- 语音、音频、GIF、贴纸和圆形视频保留最小元数据，并记录 `unsupported_media_type`。
- 扫描预估、磁盘预留、空间缺口和用户确认后下载。
- 下载暂停、恢复、取消、限流等待和有限重试。
- `.part` 检查点同时保存偏移和 SHA-256，恢复前验证内容；不一致时安全回退并重新下载。
- `FLOOD_WAIT` 和普通重试持久化 `wait_until`。
- `result.json` 和 `manifest.json` 流式写入，媒体目录分片。
- 提交前独立读回 JSON、重算全部媒体哈希、核对计数、检查孤儿/临时/Session 文件并扫描已知 API Hash 泄漏。
- SSE 支持 `Last-Event-ID` 和单调 `revision`，前端使用 SSE 触发快照刷新，15 秒轮询兜底。
- API 错误统一包含 `code`、`category`、`retryable`、`user_action` 和 `request_id`。
- 单容器多阶段构建，FastAPI 同源提供 React 页面和 API。
- Compose 仅发布宿主机回环端口，使用 `/data` 与 `/exports` 双持久化边界。
- Docker Secret 提供 256 位主密钥，API 凭据使用 AES-256-GCM 认证加密。
- 容器以非 root 用户和只读根文件系统运行，并删除 Linux capabilities。

## 已自动验证

- 后端单元与集成测试。
- 25%、50%、接近 100% 检查点中断恢复。
- `.part` 内容篡改和偏移不一致回退。
- 扫描游标中断恢复。
- 磁盘空间不足、目录锁定、Manifest/媒体篡改、孤儿文件和秘密 canary。
- 前端 TypeScript 检查与生产构建。
- Dockerfile 构建检查、真实镜像构建、健康检查、SPA 路由、Secret 密文和重启恢复。
- `tests/acceptance/verify_round1.py --self-test` 离线门禁。

## 仍需外部环境验证

- 使用真实 TB 级 Telegram 会话执行数小时或数天的长期运行验证。
- 在真实网络中验证长时间断网、限流和 Telegram DC 迁移恢复。
- 在更多 Docker Desktop/原生 Linux 环境验证卷权限并建立 CI 门禁。
- 实现跨任务媒体缓存与文件去重，避免重复导出时重新下载相同媒体。

这些项目需要真实账号、真实大数据量或最终部署环境，不能由离线 Fake Telegram 测试替代。
