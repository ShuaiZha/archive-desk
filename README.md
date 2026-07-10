# Archive Desk

> 项目状态：**Alpha**。核心导出链路已经通过自动化测试，但仍处于早期验证阶段，不建议将其作为唯一备份方案。

Archive Desk 是一个仅在本机运行的 Telegram 历史导出工具。第一轮目标是完成一条可验证的纵向链路：配置 API 凭据，使用手机号、验证码和可选的两步验证登录，选择一个会话，把消息写入 JSON，并把该会话中的图片、普通视频和普通文件下载到本地。任务必须能够暂停、重启后续传，并生成可校验的 `manifest.json`。

第一轮的权威接口、状态机、输出格式和验收门槛见 [docs/ROUND1_SPEC.md](docs/ROUND1_SPEC.md)。
当前代码已经落地的能力和仍需真实环境验证的项目见 [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)。

## 环境要求

- Windows 10/11
- Node.js 20 或更高版本
- Python 3.11 或更高版本
- 从 <https://my.telegram.org> 为本应用申请的 `api_id` 和 `api_hash`

不要使用网上共享的 API 凭据，也不要把 `.env`、Telegram `*.session` 文件或实际导出内容提交到版本库。

## 启动后端

首次安装：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

启动本地服务：

```powershell
cd backend
.\.venv\Scripts\python.exe -m archivedesk.main
```

服务必须只监听 `127.0.0.1`。开发环境默认 API 地址为 `http://127.0.0.1:8000/api/v1`。

## 启动前端

另开一个 PowerShell 窗口，在仓库根目录运行：

```powershell
npm ci
npm run dev
```

然后打开 <http://127.0.0.1:4173>。生产打包时前端应由 FastAPI 同源提供，避免放开宽泛的 CORS。

## 构建前端

```powershell
npm run build
```

## 离线验收

验收脚本只使用 Python 标准库，不需要真实 Telegram 账号：

```powershell
python tests/acceptance/verify_round1.py --self-test
```

校验某次候选导出：

```powershell
python tests/acceptance/verify_round1.py "D:\Telegram Archives\Archive Desk Export 2026-07-10"
```

它会检查消息/媒体引用闭合、计数、文件大小与 SHA-256、路径安全、残留 `.part` 文件，以及敏感字段和测试 canary 是否泄漏。完整的故障注入步骤见 [tests/README.md](tests/README.md)。

## 第一轮输出

```text
Archive Desk Export <date>/
├─ result.json
├─ manifest.json
└─ media/
   ├─ photo/<year>/<month>/<shard>/<message-id>_photo_<name>.jpg
   ├─ video/<year>/<month>/<shard>/<message-id>_video_<name>.mp4
   └─ file/<year>/<month>/<shard>/<message-id>_file_<name>.pdf
```

`result.json` 保存消息与媒体引用，`manifest.json` 保存任务统计、媒体清单以及文件大小和 SHA-256。尚未完成的 `.part` 文件只能位于后端运行时目录，不能出现在已提交的最终导出目录。旧任务中已经生成的 `index.html` 会原样保留，但新任务不再生成 HTML 查看器。

## 大规模导出

导出任务先按 250 条消息一批写入 SQLite，并持久化 Telegram 扫描游标和消息快照上界；扫描完成后检查已知媒体总大小和磁盘剩余空间，再按 500 条附件一页顺序下载。`result.json` 与 `manifest.json` 使用流式数组写入，不会把全部消息或附件清单同时装入内存。媒体目录按类型、年份和月份分片，任务发布只使用同卷原子重命名，避免 Windows 锁文件时复制整棵大型媒体目录。

无法从 Telegram 元数据获得大小的附件会在任务页提示。容量检查为已知剩余下载量额外保留至少 1 GB 或 10% 的安全空间；磁盘不足时任务保留预估结果和可恢复的 partial 目录，释放空间后可在任务页重新检查。

每个下载检查点同时保存安全偏移和 SHA-256。恢复时，文件长度和哈希都必须与数据库检查点一致，否则回退并重新下载。最终提交前会重新读取 JSON、重新计算所有媒体哈希、核对 Manifest 与磁盘文件集合，并拒绝 `.part`、Session、孤儿文件和已知秘密泄漏。

## Docker 方向

当前代码没有绑定 Windows 安装器。运行数据目录由 `ARCHIVEDESK_DATA_DIR` 指定，导出目录通过后端授权后可以映射到持久卷。最终容器化时还需要确定容器监听地址、仅本机端口发布、卷权限和同源反向代理策略；在这些安全边界确定前，默认服务仍只监听 `127.0.0.1`。

## Alpha 阶段尚未完成

- 使用真实 TB 级 Telegram 会话进行数小时或数天的长期运行验证。
- 在真实网络中验证长时间断网、限流和 Telegram DC 迁移恢复。
- 完成 Docker、CI、持久卷权限和网络访问边界设计。
- 实现跨任务媒体缓存与文件去重，避免重复导出时重新下载相同媒体。

## 许可证

本项目使用 [MIT License](LICENSE)。
