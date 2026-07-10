# 第一轮验收材料

`acceptance/verify_round1.py` 只使用 Python 标准库。它既是最终导出验证器，也是契约验证器的自测程序。

## 1. 离线自测

```powershell
python tests/acceptance/verify_round1.py --self-test
```

自测会在系统临时目录内完成以下操作：

1. 从一个确定性源文件写入部分 `.part` 和 checkpoint，模拟进程中断后按字节 offset 恢复；最终 SHA-256 必须与源文件一致。
2. 构建一个包含恶意消息文本的合法 JSON + 媒体 + Manifest 导出，并验证其闭合。
3. 分别注入媒体篡改、Manifest 计数错误、未知资源引用、路径穿越、残留 `.part` 和秘密 canary；验证器必须逐一拒绝。
4. 对 `fixtures/malicious_inputs.json` 中的危险最终路径逐一断言拒绝。

这验证了验收器和协议样例，不代表 Telegram 适配器本身已经通过恢复测试。

后端集成测试另外覆盖：扫描 250 条后持久化游标、模拟扫描中断、从游标继续直到 600 条消息闭合，以及 512 KB 媒体分块的进程重启续传。

## 2. 校验候选导出

```powershell
python tests/acceptance/verify_round1.py "D:\Telegram Archives\Archive Desk Export 2026-07-10"
```

成功时退出码为 0；任何错误退出码为 1。检查内容包括：

- `result.json` 与 `manifest.json` 的核心字段和版本；
- 消息唯一性、UTF-16 Entity 边界、消息到资源引用闭合；
- 所有资源都有终态，非下载项有原因码；
- Manifest 计数与完整性等级可由 Result 重新推导；
- 最终磁盘文件集合与 Manifest 完全相等；
- 文件大小、SHA-256 和媒体资源记录一致；
- 安全相对路径、无符号链接/junction、无 `.part` 和高风险运行时文件；
- JSON 中没有授权秘密字段，所有文件中没有测试 canary。

## 3. 无真实 Telegram 的后端故障注入

后端提供 fake Telegram adapter 后，使用 `resume_source.txt` 作为远端媒体，使用 `secret_canaries.txt` 作为 fake 登录过程中出现的瞬时秘密。Fake adapter 的消息内容应包含 `malicious_inputs.json` 中的消息和原始文件名。

对 25%、50%、99% 三个 checkpoint 分别执行：

1. 创建单会话任务并等到 `.part` 达到目标比例。
2. 直接终止后端进程，不调用暂停或清理 API。
3. 记录 SQLite 中 `committed_offset` 与 `.part` 实际长度。
4. 重启同一个后端运行目录，恢复任务。
5. 运行本文件第 2 节的候选导出校验。
6. 确认最终媒体 SHA-256 等于 `resume_source.txt`，消息唯一键没有重复，最终目录没有 `.part`。

还必须各执行一次反向故障：人为把 checkpoint 调大一块、调小一块，以及把 `.part` 末尾改写一个字节。实现必须拒绝盲目追加并安全回退或重新下载，不能生成 `SUCCEEDED` Manifest。

## 4. 秘密泄漏门禁

Fake adapter 在认证过程中返回/接收 canary，但预期导出只包含恶意消息样例，不包含 canary。运行候选导出验证后，还应对以下位置执行同一 canary 扫描：

- 前端 Local Storage、Session Storage 和 IndexedDB；
- 前后端访问日志与异常日志；
- SQLite 中非专用加密凭据字段；
- 最终导出目录；
- 诊断包。

实际账号的 API Hash、验证码或 Session 不应被复制到测试夹具。测试只能使用本目录中的虚构 canary。
