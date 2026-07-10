# 第一轮可执行规格

- 版本：`1.1.0`
- 状态：实施基线，与当前兼容输出同步
- 适用范围：单机、单用户、单账号、单会话导出

本文中的“必须”“不得”“应当”是验收要求。实现方便或界面显示成功不能替代这些要求。

当前实现补充约定：

- 创建任务使用 `dialog_id`、`media_types`、`max_file_size_mb` 和 IANA `time_zone`；`max_file_size_mb=null` 表示不限制单文件大小。
- 前端内容范围为 `photo`、`video` 和 `file`。语音、音频、GIF、贴纸与圆形视频只保留元数据并标记策略跳过。
- 下载前先进入 `awaiting_confirmation`，磁盘空间检查通过后由用户调用 `confirm`；空间变化后调用 `recheck`。
- 当前磁盘输出为兼容 Schema 1，由提交前独立验证器和离线验收器共同验证。`docs/schemas` 中的 1.0 完整资源模型保留为后续无损迁移目标。

## 1. 目标与完成定义

第一轮交付一条真实、可恢复、可验证的纵向链路：

1. 用户在后端配置自己的 Telegram `api_id` 和 `api_hash`。
2. 用户通过手机号、验证码以及可选的两步验证密码完成授权。
3. UI 从 Telegram 加载真实会话，用户选择且只能选择一个云端会话。
4. 后端导出该会话范围内的消息文本和必要元数据到 `result.json`。
5. 后端下载用户选中的图片、普通视频和普通文件，应用单文件大小上限。
6. 任务支持暂停、继续、取消；进程或电脑重启后可以从已提交检查点继续。
7. 成功提交的目录包含 `result.json`、媒体文件和闭合的 `manifest.json`。
8. 离线验收脚本通过，且每个未下载资源都有稳定、可展示的原因码。

“完成”不等于所有 Telegram 数据都可获得。第一轮不承诺秘密聊天、已删除或自毁内容、受保护内容、论坛主题完整语义、多会话导出、HTML 查看器、贴纸、GIF、语音和圆形视频。发现但不在第一轮下载范围内的媒体仍应保留最小元数据并标记 `SKIPPED_POLICY`，不得静默丢弃。

## 2. 不变量

实现必须始终维持以下不变量：

- **授权隔离**：`api_hash`、验证码、两步验证密码、授权密钥和 Telethon Session 永远不进入浏览器持久存储、URL、日志或导出目录。
- **消息幂等**：同一任务中 `(peer_key, message_id)` 唯一；重试和恢复不能生成重复消息。
- **资源闭合**：每个发现的资源都恰好有一个终态，且所有消息中的 `asset_ids` 都能解析到顶层资源。
- **写入原子性**：未完成媒体只使用 `.part`；校验后在同一文件系统中原子重命名。最终目录不得含 `.part`。
- **检查点后置**：只有数据库事务或文件块已持久化后，才能推进对应 checkpoint。
- **路径约束**：Telegram 提供的标题和文件名不能直接成为磁盘路径。所有最终路径都由后端生成，并且解析后仍位于授权输出根目录内。
- **可验证完成**：只有渲染、计数、大小、哈希、秘密扫描和 Manifest 闭合全部通过，任务才能进入 `SUCCEEDED`。

## 3. 第一轮架构边界

```text
React UI --HTTP/SSE--> FastAPI --Telethon--> Telegram
                         |   |
                         |   +--> SQLite 任务与检查点
                         +------> .part / JSON / Manifest
```

- React 是控制面，不直接创建 Telethon Client，不读取 Session，不直接写任意本地路径。
- FastAPI 只绑定 `127.0.0.1`。开发阶段只允许精确的前端 Origin；生产阶段前后端同源并关闭 CORS。
- 同一 Telegram 账号最多有一个活动 Actor 和一个导出任务。并发打开同一 SQLite Session 文件不在第一轮支持范围内。
- SQLite 是状态权威来源；内存中的进度、队列和 SSE 事件都可以由数据库状态重建。
- 输出目录由后端以不透明的 `output_root_id` 表示。浏览器不得把未经授权的任意绝对路径直接提交给下载器。

## 4. 时间与标识语义

- API 时间戳必须使用带时区的 RFC 3339；服务端事件统一输出 UTC 和 `Z`。
- `date_from` 和 `date_to` 是 `YYYY-MM-DD`，均按请求的 IANA `time_zone` 解释，且日期对用户均为包含关系。
- 后端必须把日期规范化为 `[date_from 00:00, date_to + 1 day 00:00)` 的 UTC 半开区间。
- 第一轮在任务开始时记录所选会话的 `upper_message_id`，只导出不大于该上界的消息。
- `account_id`、`job_id`、`flow_id`、`output_root_id` 是应用内部不透明 ID。
- `peer_key` 是后端生成的不透明稳定字符串，例如 `user:123`、`chat:456` 或 `channel:789`；前端不得解析其格式。
- JSON Entity 的 `offset_utf16` 和 `length_utf16` 使用 Telegram 的 UTF-16 code unit 语义，不是 Python code point 或 UTF-8 字节偏移。

## 5. HTTP 契约

所有端点位于 `/api/v1`。请求和响应使用 UTF-8 JSON；SSE 端点除外。列表响应统一为：

```json
{
  "items": [],
  "next_cursor": null
}
```

错误响应统一为：

```json
{
  "error": {
    "code": "AUTH_CODE_EXPIRED",
    "category": "AUTH",
    "message": "验证码已过期，请重新发送",
    "retryable": true,
    "user_action": "REQUEST_NEW_CODE",
    "retry_at": null,
    "request_id": "01J...",
    "details": {}
  }
}
```

`message` 可本地化，程序逻辑只能依赖稳定的 `code`、`retryable` 和 `user_action`。`details` 不得包含手机号、验证码、密码、完整路径、聊天文本、Session、access hash、file reference 或原始异常堆栈。

### 5.1 健康与 Telegram 凭据

| 方法 | 路径 | 语义 |
|---|---|---|
| `GET` | `/health` | 返回进程状态，不连接 Telegram |
| `GET` | `/telegram/credentials` | 只返回是否配置及脱敏 API ID |
| `PUT` | `/telegram/credentials` | 验证并保存 `api_id`、`api_hash` |

`PUT /telegram/credentials` 请求：

```json
{
  "api_id": 12345678,
  "api_hash": "0123456789abcdef0123456789abcdef"
}
```

凭据的 GET/PUT 响应均不得回显 `api_hash`：

```json
{
  "configured": true,
  "api_id_masked": "****5678"
}
```

### 5.2 登录流程

| 方法 | 路径 | 语义 |
|---|---|---|
| `POST` | `/auth/flows` | 规范化手机号、发送验证码并创建流程 |
| `GET` | `/auth/flows/{flow_id}` | 查询短期流程状态 |
| `POST` | `/auth/flows/{flow_id}/code` | 提交验证码 |
| `POST` | `/auth/flows/{flow_id}/password` | 仅在需要时提交 2FA 密码 |
| `POST` | `/auth/flows/{flow_id}/resend` | 在允许时间后重发验证码 |
| `DELETE` | `/auth/flows/{flow_id}` | 取消流程并清除瞬时秘密 |

创建流程请求：

```json
{"phone_number":"+8613800000000"}
```

流程响应：

```json
{
  "id": "flow_01J...",
  "state": "CODE_REQUIRED",
  "expires_at": "2026-07-10T10:05:00Z",
  "resend_available_at": "2026-07-10T10:01:00Z",
  "account_id": null
}
```

验证码请求体为 `{"code":"12345"}`，密码请求体为 `{"password":"..."}`。这两个请求体不得写入访问日志。成功后流程为 `AUTHORIZED` 并返回 `account_id`；如果启用了两步验证，提交验证码后状态为 `PASSWORD_REQUIRED`。

登录流程状态集合：

```text
CODE_REQUIRED -> PASSWORD_REQUIRED -> AUTHORIZED
       |                 |
       +-----------------+-> EXPIRED / CANCELLED / FAILED
```

### 5.3 账号和会话

| 方法 | 路径 | 语义 |
|---|---|---|
| `GET` | `/accounts` | 已授权账号列表，使用 `items` 包装 |
| `DELETE` | `/accounts/{account_id}` | Telegram logout 并删除本地 Session |
| `GET` | `/accounts/{account_id}/dialogs` | 分页读取真实会话 |
| `POST` | `/accounts/{account_id}/dialogs/refresh` | 显式刷新会话缓存 |

会话查询支持 `cursor`、`limit`（1–100）、`query` 和 `type`。会话项最小结构：

```json
{
  "peer_key": "channel:789",
  "type": "CHANNEL",
  "title": "示例频道",
  "username": "example",
  "message_count_estimate": 1200,
  "media_count_estimate": null,
  "can_export": true,
  "unsupported_reason": null
}
```

秘密聊天必须返回 `can_export=false` 和 `unsupported_reason="SECRET_CHAT_LOCAL_ONLY"`，或在第一轮列表中明确排除；不得让用户创建一个必然为空却显示成功的任务。

### 5.4 输出根目录

| 方法 | 路径 | 语义 |
|---|---|---|
| `GET` | `/output-roots` | 返回当前授权根目录，使用 `items` 包装 |
| `POST` | `/output-roots/pick` | 通过本机原生目录选择器授权目录 |

响应只需要给 UI 显示脱敏路径和不透明 ID：

```json
{
  "id": "root_01J...",
  "display_path": "D:\\Telegram Archives",
  "writable": true,
  "free_bytes": 53687091200
}
```

纯浏览器开发环境无法提供可靠的原生目录选择器时，可以只暴露由后端配置的默认根目录；不得退化为允许网页控制任意绝对路径。

### 5.5 导出任务

创建任务：`POST /export-jobs`

```json
{
  "account_id": "acct_01J...",
  "peer_key": "channel:789",
  "output_root_id": "root_01J...",
  "date_from": "2025-01-01",
  "date_to": "2026-07-10",
  "time_zone": "Asia/Shanghai",
  "include_photos": true,
  "include_documents": true,
  "max_file_size": 4294967296
}
```

`date_from` 和 `date_to` 可同时为 `null`，表示全部历史；不得只传其中一个。`max_file_size` 的单位固定为字节，范围为 `0..4294967296`；`0` 表示不下载任何媒体但仍导出资源元数据。必须至少选择 `include_photos` 或 `include_documents` 之一，除非 `max_file_size=0`。

创建接口应接受 `Idempotency-Key` 请求头。相同账号、相同 key 和相同请求体返回原任务；相同 key 配不同请求体返回 `409 IDEMPOTENCY_CONFLICT`。成功创建返回 `202` 和任务对象。

| 方法 | 路径 | 语义 |
|---|---|---|
| `GET` | `/export-jobs` | 任务列表，使用 `items` 包装 |
| `GET` | `/export-jobs/{job_id}` | 任务快照 |
| `POST` | `/export-jobs/{job_id}/actions/start` | 启动已创建任务 |
| `POST` | `/export-jobs/{job_id}/actions/pause` | 请求在安全点暂停 |
| `POST` | `/export-jobs/{job_id}/actions/resume` | 从 checkpoint 继续 |
| `POST` | `/export-jobs/{job_id}/actions/cancel` | 请求取消，不提交最终目录 |
| `POST` | `/export-jobs/{job_id}/actions/retry` | 重试可恢复失败 |
| `GET` | `/export-jobs/{job_id}/events` | SSE 进度事件 |

任务快照最小结构：

```json
{
  "id": "job_01J...",
  "account_id": "acct_01J...",
  "peer_key": "channel:789",
  "status": "RUNNING",
  "phase": "DOWNLOADING",
  "revision": 18,
  "created_at": "2026-07-10T09:00:00Z",
  "updated_at": "2026-07-10T09:05:00Z",
  "wait_until": null,
  "progress": {
    "messages_discovered": 1200,
    "messages_persisted": 1200,
    "assets_discovered": 85,
    "assets_terminal": 20,
    "bytes_expected": 104857600,
    "bytes_downloaded": 20971520,
    "bytes_per_second": 5242880
  },
  "last_error": null
}
```

如果总量未知，对应字段为 `null`，UI 不得伪造总体百分比。

SSE 事件使用 `id:` 支持 `Last-Event-ID` 重连，`data:` 的 JSON 结构为：

```json
{
  "schema_version": 1,
  "type": "job.progress",
  "job_id": "job_01J...",
  "revision": 18,
  "occurred_at": "2026-07-10T09:05:00Z",
  "data": {}
}
```

客户端必须按 `revision` 丢弃乱序或重复事件，并在断线后重新 GET 任务快照。

## 6. 任务状态机

主状态：

```text
CREATED -> QUEUED -> RUNNING <-> WAITING
                       |
                       +-> PAUSING -> PAUSED -> QUEUED
                       +-> CANCELLING -> CANCELLED
                       +-> FAILED -> QUEUED (显式 retry)
                       +-> SUCCEEDED
```

内部阶段按以下顺序单调推进：

```text
PREFLIGHT -> SNAPSHOT -> ENUMERATING -> DOWNLOADING
          -> RENDERING -> VERIFYING -> COMMITTING -> FINALIZING
```

- `WAITING` 用于 `FLOOD_WAIT`、Takeout 延迟等有确定恢复时间的条件，必须保存 `wait_until`。
- `PAUSING` 表示等待当前数据库事务或文件块完成。到达安全点后持久化 checkpoint，再进入 `PAUSED`。
- 进程重启时，过期 lease 的 `RUNNING/PAUSING/WAITING` 任务必须恢复为可调度状态；不得因为进程退出而标记成功。
- `CANCELLED` 和 `FAILED` 不得发布最终目录。保留或清理运行时 `.part` 必须是显式策略。
- `SUCCEEDED` 是终态，只能在最终目录原子提交和 Manifest 验证后进入。
- 非法转换返回 `409 JOB_INVALID_TRANSITION`，不得静默忽略。

## 7. 断点下载协议

每个资源至少保存：`asset_id`、来源 `(peer_key, message_id)`、预期大小、已提交 offset、临时路径、当前状态和最后错误。

1. 写入 `asset_id.part` 前先把资源记录提交为 `DOWNLOADING`。
2. 每个网络块完整写入并刷新后，再提交新的 `committed_offset`。
3. 恢复时比较 `.part` 实际大小和 `committed_offset`。不一致时只能回退到最后一个已知安全边界，不能把较大的文件长度直接当成可信 checkpoint。
4. 恢复前重新计算已有 `.part` 的 SHA-256；第一轮允许 O(n) 重读，禁止持久化不可移植的 `hashlib` 内部状态。
5. `FILE_REFERENCE_EXPIRED/INVALID` 时重新读取来源消息，取得新引用后从同一安全 offset 重试。
6. 下载完成后校验预期大小并计算 SHA-256，随后同目录原子重命名为最终安全文件名。
7. 数据库把资源标记为 `DOWNLOADED` 只能发生在最终文件存在且大小、哈希已记录之后。

远端原始文件名只保存为 JSON 显示字段。磁盘名建议为 `<asset_id>.<可信扩展名>`；扩展名根据受信 MIME 映射和 Telegram 媒体类型决定，不能信任双扩展名。

## 8. 输出目录与 Schema

最终目录：

```text
Archive Desk Export <date>/
├─ result.json
├─ manifest.json
└─ media/
   └─ <safe-asset-id>.<safe-extension>
```

运行时数据库、日志、Session、`.part`、access hash、file reference 和绝对路径都不得复制到最终目录。

权威 JSON Schema：

- [`docs/schemas/round1-result.schema.json`](schemas/round1-result.schema.json)
- [`docs/schemas/round1-manifest.schema.json`](schemas/round1-manifest.schema.json)

核心关系：

- `result.json.messages[].asset_ids[]` 必须引用 `result.json.assets[].asset_id`。
- 每个顶层资源至少被一条消息引用；`asset_id` 唯一。
- 每个资源状态必须是 `DOWNLOADED`、`SKIPPED_SIZE`、`SKIPPED_POLICY`、`UNAVAILABLE` 或 `FAILED`。
- `DOWNLOADED` 必须有安全相对路径、实际大小和小写 SHA-256；其他状态不得伪造本地文件路径。
- Manifest 的 `files` 不包含 `manifest.json` 自身，但必须恰好覆盖最终目录内所有其他普通文件。
- `result.json` 必须以 `role=RESULT` 出现在 Manifest；每个已下载资源必须以 `role=MEDIA` 出现。
- Manifest 计数必须由 `result.json` 重新计算得到，不能以任务内存中的计数代替。

完整性按以下规则推导：

| 条件 | `completeness` |
|---|---|
| 所有资源均 `DOWNLOADED`，或没有资源 | `FULL` |
| 非下载资源只有 `SKIPPED_SIZE/SKIPPED_POLICY` | `POLICY_FILTERED` |
| 任意资源为 `UNAVAILABLE/FAILED` | `PARTIAL` |

第一轮只发布 `execution_status="SUCCEEDED"` 的最终 Manifest。失败或取消任务的诊断状态保留在后端运行时数据库，不发布一个看似完整的导出目录。

## 9. 文件名与消息安全

所有最终相对路径必须：

- 使用 `/` 作为 JSON 路径分隔符，不能为空、绝对路径、UNC 路径或盘符路径；
- 不含 `.`、`..`、反斜杠、控制字符、双向文本控制符或 NTFS ADS 的冒号；
- 不使用 Windows 保留名 `CON/PRN/AUX/NUL/COM1..9/LPT1..9`；
- 组件不能以点或空格结尾，且解析后仍在输出根目录；
- 任一路径组件不能是符号链接或 Windows junction/reparse point；
- 第一轮 Windows 目标下每个组件不超过 120 字符，完整相对路径不超过 240 字符。

消息文本和 Entity 必须作为数据保存，不能执行。第一轮没有 HTML 渲染器；日志和错误消息不得插入原始消息文本。`tests/fixtures/malicious_inputs.json` 是必须保留的回归输入集合。

## 10. 错误分类

| 分类 | 示例稳定代码 | 默认策略 |
|---|---|---|
| `CONFIG` | `API_CREDENTIALS_MISSING`, `INVALID_DATE_RANGE`, `OUTPUT_ROOT_NOT_AUTHORIZED` | 用户修正，不自动重试 |
| `AUTH` | `AUTH_CODE_INVALID`, `AUTH_CODE_EXPIRED`, `AUTH_PASSWORD_REQUIRED`, `AUTH_PASSWORD_INVALID`, `SESSION_REVOKED` | 受限重试或重新登录 |
| `TELEGRAM_WAIT` | `FLOOD_WAIT`, `TAKEOUT_INIT_DELAY` | 持久化 `retry_at`，进入 `WAITING` |
| `TELEGRAM_TRANSIENT` | `NETWORK_TIMEOUT`, `DC_MIGRATE`, `FILE_REFERENCE_EXPIRED` | 有上限的指数退避；刷新引用/切换 DC |
| `TELEGRAM_PERMANENT` | `MEDIA_UNAVAILABLE`, `CHAT_INACCESSIBLE`, `PROTECTED_CONTENT` | 资源终态或任务失败，不无限重试 |
| `STORAGE` | `OUTPUT_NOT_WRITABLE`, `DISK_FULL`, `PATH_UNSAFE`, `FILE_LOCKED` | 暂停并要求用户处理 |
| `INTEGRITY` | `SIZE_MISMATCH`, `HASH_MISMATCH`, `MANIFEST_NOT_CLOSED`, `CHECKPOINT_MISMATCH` | 不提交最终目录 |
| `JOB` | `JOB_BUSY`, `JOB_INVALID_TRANSITION`, `IDEMPOTENCY_CONFLICT`, `CANCELLED_BY_USER` | 依状态处理 |
| `INTERNAL` | `UNEXPECTED_ERROR` | 生成脱敏 request ID，不返回堆栈 |

建议 HTTP 映射：参数错误 `422`，未授权 `401`，资源不可见 `404`，状态冲突 `409`，认证/Telegram节流 `429`，磁盘不足 `507`，其余不可预期错误 `500`。

自动重试必须有上限、抖动和持久化的下一次时间；`FLOOD_WAIT` 的服务端等待时间不能被普通指数退避覆盖。认证发送验证码和密码验证不得自动无限重试。

## 11. Manifest 闭合算法

提交前必须从磁盘和 `result.json` 重新构建事实集合：

1. 枚举最终暂存目录中的所有普通文件，排除 `manifest.json`。
2. 拒绝符号链接、junction、`.part`、Session、数据库、环境文件和日志。
3. 重新计算每个文件的字节数和 SHA-256。
4. 验证所有消息引用、资源唯一性、资源终态和状态必需字段。
5. 从资源状态重新计算计数和 `completeness`。
6. 生成 Manifest，使 `files` 与步骤 1 的集合完全相等。
7. 再读回 Manifest，使用独立验证器执行同样检查。
8. 验证通过后原子提交目录，再把任务置为 `SUCCEEDED`。

Manifest 不自我哈希。若未来需要证明 Manifest 本身未变，应生成目录之外的签名或摘要文件，而不是建立递归自引用。

## 12. 验收清单

### 功能

- [ ] 未配置 API 凭据时不能开始登录，GET 永不回显 API Hash。
- [ ] 手机号、验证码、2FA 和注销流程均有真实后端状态，不使用 UI 模拟计时器。
- [ ] 会话列表来自真实授权账号，第一轮只能选择一个可导出云端会话。
- [ ] 全部历史和包含式日期范围均按指定时区正确工作。
- [ ] 图片、普通视频和普通文件按开关及字节上限下载；超限项为 `SKIPPED_SIZE`。
- [ ] `result.json` 保留消息文本、caption、Entity、回复关系和资源引用。
- [ ] 页面展示真实的消息、资源和字节进度；总量未知时不显示虚假百分比。

### 恢复与一致性

- [ ] 在枚举消息、写数据库、下载文件、渲染 JSON、校验 Manifest 各阶段强杀进程，重启后均能收敛。
- [ ] 下载到 25%、50%、99% 时强杀，恢复后最终文件哈希与一次性下载一致，网络重取不重复写入文件。
- [ ] 同一 Idempotency-Key 重放不创建第二个任务。
- [ ] 同一账号不能出现两个活动 Telegram Client/导出任务。
- [ ] 数据库 checkpoint 大于或小于 `.part` 实际长度时均拒绝盲目续传并安全回退。
- [ ] 磁盘满、文件被占用和目录权限丢失不会生成成功 Manifest。

### 输出完整性

- [ ] 两份 JSON 通过权威 Schema 和 `tests/acceptance/verify_round1.py`。
- [ ] 消息 ID、资源 ID、消息到资源引用、Manifest 文件和计数全部闭合。
- [ ] 每个文件重新计算的大小和 SHA-256 与 Manifest 一致。
- [ ] 最终目录没有孤儿文件、`.part`、Session、数据库、日志或绝对路径。
- [ ] 每个未下载资源都有稳定原因码，且 `completeness` 推导正确。

### 安全与隐私

- [ ] FastAPI 只监听 `127.0.0.1`，生产同源；写接口校验 Host/Origin 和本地控制会话。
- [ ] API Hash、验证码、2FA、phone code hash、auth key、Session、access hash、file reference 和 Takeout ID 不进入浏览器存储、URL、日志和最终目录。
- [ ] `malicious_inputs.json` 中的文件名不能逃逸输出根、覆盖特殊文件或形成 Windows ADS。
- [ ] 恶意消息文本原样作为 JSON 数据保存，但不会出现在日志或被执行。
- [ ] 注销会调用 Telegram logout，并删除本地账号 Session 与瞬时认证数据。

### 离线门禁

以下命令必须返回退出码 0：

```powershell
python tests/acceptance/verify_round1.py --self-test
npm run build
```

对每个候选导出还必须运行：

```powershell
python tests/acceptance/verify_round1.py "<export-directory>"
```

离线自测验证契约验证器自身能发现：断点重复/错位、Manifest 计数不闭合、文件篡改、路径穿越、残留 `.part` 和 canary 秘密泄漏。真实 Telegram 的完整性仍需在受控测试会话中与消息 ID 基准集合比较，离线脚本不能替代该项。
