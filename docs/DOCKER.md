# Docker 部署

Archive Desk 的容器版本使用一个镜像同时提供 React 静态页面和 FastAPI API。浏览器只访问宿主机回环地址，容器状态与导出内容使用两个独立的持久化边界。

## 架构

```text
http://127.0.0.1:4173
          |
          v
Archive Desk container :8000
  |- /app/static     React 静态页面，只读
  |- /data           SQLite、Session、加密凭据
  `- /exports        导出结果
```

- `/data` 使用 Compose 命名卷 `archive-desk_archivedesk-data`。
- `/exports` 绑定到仓库根目录的 `./exports`，便于在宿主机直接访问结果。
- `/run/secrets/archivedesk_master_key` 由 Compose Secret 只读挂载。
- 容器内部监听 `0.0.0.0:8000`，宿主机默认只发布 `127.0.0.1:4173`。

## 前置条件

- Docker Desktop，包含 Docker Compose v2
- Windows PowerShell 5.1 或 PowerShell 7
- 当前仓库源码

确认命令可用：

```powershell
docker --version
docker compose version
```

## 首次启动

Windows PowerShell 在仓库根目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\New-DockerSecret.ps1
New-Item -ItemType Directory -Path .\exports -Force | Out-Null
docker compose up --build -d
```

如果 `docker compose version` 不可用，但 `docker-compose version` 可用，请把本文命令中的 `docker compose` 替换为 `docker-compose`。

macOS/Linux：

```bash
mkdir -p .docker exports
umask 077
openssl rand -base64 32 > .docker/archivedesk_master_key
docker compose up --build -d
```

打开 <http://127.0.0.1:4173>，重新配置 Telegram API ID/API Hash 并完成账号登录。

Windows DPAPI 凭据不能直接复制到 Linux 容器。Telegram Session 也不建议跨安全边界手动迁移，容器首次运行应重新授权。

## Docker Secret

生成脚本会在 `.docker/archivedesk_master_key` 创建一个随机 256 位主密钥，并以标准 Base64 保存。该目录已被 Git 忽略，也不会进入 Docker 构建上下文。

容器使用该密钥通过 AES-256-GCM 加密 `/data/credentials.bin`。每次保存使用独立的随机 96 位 nonce，并对密文执行认证；密钥错误、文件截断或密文篡改都会导致凭据读取失败。

必须安全备份 `.docker/archivedesk_master_key`：

- 丢失密钥后，现有 `credentials.bin` 无法恢复。
- 不要在已经存在 `/data` 数据卷时重新生成或替换密钥。
- 不要把密钥写入 `compose.yaml`、环境变量、镜像或版本库。

## 持久化边界

| 内容 | 容器路径 | 宿主机位置 | 生命周期 |
| --- | --- | --- | --- |
| 数据库、Session、加密凭据 | `/data` | Docker 命名卷 | `docker compose down` 后保留 |
| 导出结果 | `/exports` | `./exports` | 普通宿主机目录，需单独备份 |
| 主密钥 | `/run/secrets/archivedesk_master_key` | `.docker/archivedesk_master_key` | 只读挂载，需安全备份 |

`docker compose down -v` 会删除 `/data` 命名卷。除非确认不再需要任务状态和 Telegram Session，否则不要使用 `-v`。

## 常用命令

查看状态：

```powershell
docker compose ps
```

查看日志：

```powershell
docker compose logs -f app
```

停止但保留数据：

```powershell
docker compose down
```

重新构建：

```powershell
docker compose up --build -d
```

## 容器安全边界

- 根文件系统只读，只有 `/data`、`/exports` 和内存中的 `/tmp` 可写。
- 运行用户固定为非 root UID/GID `10001`。
- 删除所有 Linux capabilities，并启用 `no-new-privileges`。
- 前端和 API 同源，不需要开放 CORS。
- “打开导出文件夹”在容器模式下隐藏；页面显示并复制的是容器路径，宿主机文件位于 `./exports`。

## Linux 宿主机权限

Docker Desktop for Windows 通常会自动处理 `./exports` 的共享权限。原生 Linux 如遇写入失败，可在启动前执行：

```bash
mkdir -p exports
sudo chown -R 10001:10001 exports
```

## 故障排查

### Secret 不存在

如果 Compose 报告 `.docker/archivedesk_master_key` 不存在，重新执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\New-DockerSecret.ps1
```

仅在第一次部署或确认旧数据不再需要时生成。脚本不会覆盖已有密钥。

### 容器健康检查失败

```powershell
docker compose ps
docker compose logs app
```

重点检查 Secret 文件、`/data` 和 `/exports` 权限，以及端口 `4173` 是否已被占用。

### 需要更换宿主机端口

设置 `ARCHIVEDESK_WEB_PORT` 后再启动，回环地址不会改变：

```powershell
$env:ARCHIVEDESK_WEB_PORT = "5173"
docker compose up -d
```

不要把 `compose.yaml` 中的宿主机地址改成 `0.0.0.0`，除非已经增加独立的身份认证、TLS 和反向代理安全边界。
