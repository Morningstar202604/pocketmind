# 安全策略

## 报告漏洞

如果发现安全漏洞，请**不要**在公开 Issue 中披露细节，以免被恶意利用。请通过以下方式私下联系维护者：

- 在[仓库主页](https://gitcode.com/badhope/pocketmind)提 Issue 说明「发现安全问题，请求私下沟通渠道」（不写细节）
- 或通过 GitHub 镜像 [X33834/pocketmind](https://github.com/X33834/pocketmind) 的 Security Advisories 私下报告

我们会尽快响应、确认并修复，修复后再公开披露细节。

## 支持的版本

仅最新发布版本（`main` 分支最新提交 / 最新 tag）接受安全修复。

## 安全模型（部署前请读完）

本项目是**单用户、单机**的本地 Agent，安全边界与多租户服务不同：

| 边界 | 机制 | 代码位置 |
|---|---|---|
| 网络 | 默认只绑 `127.0.0.1`；`--lan` / `--host 0.0.0.0` 强制要求 `server.token`，否则拒绝启动 | `agentd/main.py:1478-1492` |
| 鉴权 | 除 `/api/health` 与静态资源外全部端点校验 `Authorization: Bearer`，常量时间比较 | `agentd/main.py:560-580` |
| 跨站 | 带 `Origin` 的请求必须同主机，覆盖所有方法（防无 body 的 simple request 静默写操作） | `agentd/main.py:516-557` |
| 工具审批 | 服务端强制：`safe` 放行、`write`/`danger` 需用户确认，超时自动拒绝；外部 MCP 工具最低 `write` | `agentd/permissions.py`、`agentd/approval.py` |
| 文件白名单 | 仅 `$HOME` / `~/storage` / `/sdcard`；`.agent`、`.ssh`、`.gnupg` 及数据目录一律拒绝（含子路径与符号链接解析） | `agentd/tools/files.py:59-71` |
| SSRF | `read_url` 解析 DNS 后按 IP 段判定 + 端口白名单 + 重定向逐跳校验 | `agentd/tools/net.py:207-270` |
| 密钥 | API Key / 令牌只存本机 `config.json`（600），任何 API 回传均打码；导出不含密钥 | `agentd/config.py`、`agentd/routes/__init__.py` |
| 数据 | SQLite 目录 700 / 文件 600，WAL 同权限 | `agentd/memory.py:62-74` |

## 已知边界（不是漏洞，但会影响你的判断）

- **提示注入**：`read_url` / `read_sms` 等工具返回的外部内容可能包含指令。防线是审批门与工具收窄，
  不是内容过滤。不要在 `auto` 权限模式下让 agent 处理不可信来源。
- **`run_shell` 是 `danger` 级**：审批后执行任意命令；批准前请确认命令内容。
- **局域网模式**：`--lan` 意味着同一网络的任何设备只要拿到令牌就能调用全部 API（含 shell）。
  不要在不可信网络开启；公网部署请在前面加 HTTPS 反代（见 `DEPLOY.md`）。
- **真机差异**：termux-api 的部分能力（短信/定位/传感器）在模拟器与桌面不可用，
  运行 `GET /api/diagnostics` 或看 `docs/capability-matrix.md` 确认。

## 依赖与供应链

- 运行时依赖见 `agentd/requirements.txt`，开发依赖见 `agentd/requirements-dev.txt`；
  关键包锁主版本（`tests/check_requirements.py` 会校验）。
- 依赖漏洞扫描：`python smoke/p22_dep_audit_test.py`（走 OSV.dev API，需外网），
  或在门禁上加 `--with-dep-audit`。
- GitHub 镜像启用 Dependabot（npm / pip / actions，按月分组更新）。
