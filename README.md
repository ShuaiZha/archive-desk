# Archive Desk

本地运行的 Telegram 历史与媒体导出工具，面向需要长时间、可恢复、可校验导出任务的用户。

[![Status: Alpha](https://img.shields.io/badge/status-alpha-f59e0b.svg)](docs/IMPLEMENTATION_STATUS.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-2563eb.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4.svg)](#系统要求)

> [!WARNING]
> Archive Desk 当前处于 Alpha 阶段。核心导出链路已经通过自动化测试，但真实 TB 级会话和极端网络环境仍在验证中。请勿将其作为唯一备份方案。

Archive Desk 使用 Telegram API 读取当前账号有权访问的云端会话。它会先扫描消息和附件、估算下载量与磁盘空间，再由用户确认下载。任务支持暂停、进程重启后恢复，并在结束时生成带 SHA-256 的完整性清单。

## 为什么做这个项目

大规模历史导出不仅是“把文件下载下来”。它还需要处理消息范围、磁盘容量、长时间限流、网络中断、进程重启、临时文件、重复进度和最终结果校验。

Archive Desk 把这条链路拆成四个清晰阶段：

```text
任务配置  ->  扫描预估  ->  下载  ->  完成与校验
```

所有配置、任务状态和导出结果均保存在本机，不提供云端中转服务。

## 核心能力

| 能力 | 当前实现 |
| --- | --- |
| Telegram 登录 | API ID/API Hash、手机号、验证码、可选两步验证 |
| 会话选择 | 搜索、类型筛选、刷新、最早和最晚消息时间查询 |
| 历史范围 | 全部历史或指定日期范围，按明确的 IANA 时区解释 |
| 下载内容 | 图片、普通视频、普通文件；消息文本与最小元数据始终保存 |
| 文件限制 | 单文件大小上限，支持无限制 |
| 下载前检查 | 附件扫描、实时预估、磁盘空间和安全预留检查 |
| 长任务控制 | 暂停、继续、取消、重试、`FLOOD_WAIT` 等待状态 |
| 断点恢复 | `.part` 文件、数据库安全偏移和 SHA-256 检查点联合校验 |
| 大规模处理 | 消息批量写入、附件分页、流式生成 JSON、媒体目录分片 |
| 完整性验证 | 重新计算媒体哈希，核对 Manifest、计数、引用和磁盘文件集合 |
| 本地界面 | React + Fluent UI 多步骤任务界面，SSE 更新和轮询兜底 |

## 当前导出范围

会下载：

- 消息文本和必要元数据
- Telegram 图片
- 普通视频附件
- 普通文档和其他文件

会识别并记录、但当前不下载：

- 语音和音频
- GIF
- 贴纸
- 圆形视频消息
- 其他不在当前媒体策略内的附件

当前不承诺：

- Secret Chat、已经删除或自毁的内容
- 受保护或 Telegram API 不再提供的内容
- 论坛主题的完整语义还原
- 多会话批量导出
- HTML 查看器
- 跨任务媒体缓存与文件去重

## 系统要求

- Windows 10 或 Windows 11
- Python 3.11 或更高版本
- Node.js 20 或更高版本
- Git
- 从 [my.telegram.org](https://my.telegram.org) 申请的个人 `api_id` 和 `api_hash`

不要使用网上共享的 Telegram API 凭据，也不要把 API Hash、Telegram Session 或实际导出内容提交到版本库。

## 快速开始

### 1. 获取源码

```powershell
git clone https://github.com/ShuaiZha/archive-desk.git
cd archive-desk
```

### 2. 安装并启动后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m archivedesk.main
```

后端默认监听 `http://127.0.0.1:8000`。服务只允许绑定回环地址，不会直接监听局域网网卡。

### 3. 安装并启动前端

另开一个 PowerShell 窗口：

```powershell
cd archive-desk
npm ci
npm run dev
```

打开 <http://127.0.0.1:4173>。

### 4. 完成首次配置

1. 在设置页填写自己的 Telegram API ID 和 API Hash。
2. 输入手机号并提交 Telegram 验证码。
3. 如果账号启用了两步验证，再输入 2FA 密码。
4. 选择会话，配置历史范围、媒体类型、文件大小限制和保存目录。
5. 先执行扫描预估，确认磁盘空间后开始下载。

## 导出结果

每个任务使用独立目录，目录名包含会话名称和任务 ID：

```text
ArchiveDesk-<conversation>-<job-id>/
├─ result.json
├─ manifest.json
└─ media/
   ├─ photo/<year>/<month>/<shard>/<message-id>_photo_<name>
   ├─ video/<year>/<month>/<shard>/<message-id>_video_<name>
   └─ file/<year>/<month>/<shard>/<message-id>_file_<name>
```

- `result.json` 保存消息、元数据和媒体引用。
- `manifest.json` 保存任务统计、媒体状态、文件大小、路径和 SHA-256。
- 未完成的 `.part` 文件只存在于任务运行目录，不会进入成功发布的最终目录。

可以使用仓库内的验收脚本检查任意候选导出：

```powershell
python tests\acceptance\verify_round1.py "D:\Telegram Archives\ArchiveDesk-example-12345678"
```

## 大规模导出设计

- 扫描开始时固定消息快照上界，恢复任务时不会混入扫描开始后的新消息。
- 消息按 250 条批量写入 SQLite，避免把完整历史保存在内存中。
- 附件按 500 条分页处理，`result.json` 和 `manifest.json` 使用流式数组写入。
- 媒体按照类型、年份、月份和分片目录组织，避免超大单目录。
- 容量检查基于剩余下载量，并额外保留至少 1 GB 或 10% 的安全空间。
- 每个持久化断点同时记录安全偏移和 SHA-256；文件被修改时会安全回退或重新下载。
- 最终目录通过同卷原子重命名发布，避免完成阶段再次复制整棵媒体目录。

## 隐私与安全

- 后端强制监听 `127.0.0.1`、`localhost` 或 `::1`。
- Windows 上的 API ID/API Hash 使用当前 Windows 用户绑定的 DPAPI 加密保存。
- Telegram Session、SQLite 数据库和加密凭据默认保存在 `%LOCALAPPDATA%\ArchiveDesk`。
- Session 文件等同于已登录账号凭据，不应复制、上传或分享。
- 导出内容只写入用户明确授权并通过写入验证的本机目录。
- 最终完整性检查会拒绝残留 `.part`、Session、孤儿文件和已知秘密泄漏。
- 仓库已忽略 `.runtime/`、`download/`、`*.session`、数据库、构建产物和依赖目录。

当前正式支持范围是 Windows。非 Windows 代码路径尚未接入系统钥匙串，不建议用于保存真实 Telegram 凭据。

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ARCHIVEDESK_DATA_DIR` | `%LOCALAPPDATA%\ArchiveDesk` | 数据库、凭据和 Session 保存目录 |
| `ARCHIVEDESK_HOST` | `127.0.0.1` | 仅接受回环地址 |
| `ARCHIVEDESK_PORT` | `8000` | 后端开发端口 |
| `VITE_DEV_BACKEND_ORIGIN` | `http://127.0.0.1:8000` | Vite 开发代理目标 |

## 开发与验证

以下命令均从仓库根目录执行。

前端类型检查和生产构建：

```powershell
npm run build
```

后端单元与集成测试：

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests
```

离线安全验收不需要真实 Telegram 账号：

```powershell
.\backend\.venv\Scripts\python.exe tests\acceptance\verify_round1.py --self-test
```

验收覆盖路径穿越、Windows 保留名称、NTFS ADS、断点偏移和哈希不一致、媒体篡改、Manifest 闭合、孤儿文件、Session 残留及秘密 canary 泄漏。

## 项目结构

```text
archive-desk/
├─ src/                         React 前端
├─ backend/archivedesk/         FastAPI、Telethon、SQLite 和导出任务引擎
├─ backend/tests/               后端单元与集成测试
├─ tests/acceptance/            离线导出完整性验收
├─ docs/schemas/                result/manifest JSON Schema
├─ docs/ROUND1_SPEC.md          第一轮行为和输出规范
└─ docs/IMPLEMENTATION_STATUS.md 当前能力与验证状态
```

进一步资料：

- [当前实施状态](docs/IMPLEMENTATION_STATUS.md)
- [第一轮可执行规格](docs/ROUND1_SPEC.md)
- [后端说明](backend/README.md)
- [验收与故障注入](tests/README.md)

## Alpha 阶段尚未完成

- 使用真实 TB 级 Telegram 会话进行数小时或数天的长期运行验证。
- 在真实网络中验证长时间断网、限流和 Telegram DC 迁移恢复。
- 完成 Docker、CI、持久卷权限和网络访问边界设计。
- 实现跨任务媒体缓存与文件去重，避免重复任务重新下载相同媒体。

这些项目需要真实账号、真实大数据量或最终部署环境，不能由 Fake Telegram 自动化测试替代。

## 参与贡献

欢迎通过 Issue 报告可复现的问题、讨论设计方案或提交 Pull Request。

提交代码前请至少运行：

```powershell
npm run build
.\backend\.venv\Scripts\python.exe -m pytest backend\tests
.\backend\.venv\Scripts\python.exe tests\acceptance\verify_round1.py --self-test
```

报告问题时请删除手机号、API Hash、Session、会话名称、消息内容和本机绝对路径。不要上传真实导出目录来复现问题；请使用最小化的模拟数据。

## 免责声明

Archive Desk 是独立的开源项目，与 Telegram 官方无隶属、授权或背书关系。使用者应只导出自己有权访问的数据，并自行确认其使用方式符合适用法律、组织政策和 Telegram 的相关规则。

## 许可证

本项目使用 [MIT License](LICENSE)。
