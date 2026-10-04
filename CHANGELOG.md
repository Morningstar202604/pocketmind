# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 修复

- **一键部署链路走不通**：`docker-compose.yml` / `Dockerfile` 一直传 `AGENT_TOKEN`，但没有任何代码读取它；
  容器以 `--lan` 启动时因"未配置访问令牌"直接 `SystemExit(1)`，配合 `restart: unless-stopped` 就是无限重启。
  现在启动时会用 `AGENT_TOKEN` **首次**初始化 `server.token`（已有配置则以 `config.json` 为准）。
- **镜像缺配方数据**：`Dockerfile` 只 COPY `agentd/`，而预设配方按 `仓库根/config/recipes.json` 读取，
  导致容器里 Jobs 面板配方静默为空、`/api/recipes/{id}/apply` 404。已补 `COPY config/`。
- **保存设置时明文回传令牌**：`GET /api/settings` 刻意把 `server.token` / `tg_token` 打码，
  但 `PUT` 的响应把未打码的原始配置整包返回——保存一次即泄漏。改为 GET/PUT 共用 `_masked_settings()`。
- **配方实例化绕过校验**：`/api/recipes/{id}/apply` 直接调 `scheduler.create()`，
  坏 cron/interval 表达式会在 APScheduler 内抛 `ValueError` 变成 500。现与手工建任务共用
  `_validate_job_payload()`，非法输入统一 400。
- **`--home` 静默失效**：`Settings` 是模块级单例，`path` 在 import 时就按当时的 `AGENT_HOME` 定死了，
  `main()` 里只改环境变量不会搬走配置与 `*.db`。现在给定 `--home` 会显式重定位并重新加载。
- **前端 MCP 配置保存被丢弃**：设置面板的 MCP 文本框写入 `form.server.mcp`，
  但 `save()` 只提交 `token` 与 `approval_timeout`，后端其实支持 `mcp` 字段——用户改完保存等于没改。
- **前端 MCP 参数切分错误**：`名称 | 命令 | 参数` 的参数段整段当成一个 argv，
  多参数服务器（如 `npx -y pkg /sdcard`）根本起不来。现按空白切分为多个 argv。
- **设置面板 401 死锁**：配置了令牌后本机也被拦（`check_token` 只看令牌是否非空，不区分 `--lan`），
  而前端 `.catch(() => {})` 把 401 吞掉，面板永远停在"加载中…"，既看不到原因也没有输入入口。
  现在 401 会显示令牌输入框（解锁后自动重载），其他错误显示真实原因；
  并新增 `python -m agentd.main --clear-token` 作为忘记令牌时的本机恢复入口。

### 测试

- 新增 `agentd/web/src/components/settingsPanel.test.tsx`：MCP 保存契约（含"未编辑不能丢数据"）与 401 门禁。
- `tests/p3_robustness_unit.py` 增加任务/配方校验与响应打码断言（13 → 23 项）。
- `tests/m5_regression.py`、`tests/p3_perf_leak_test.py`：chmod 语义与进程残留探测等 POSIX 专用断言
  在非 POSIX 平台记为「跳过」，不再产生假红；同时不再要求 Windows 本机必须有 termux 桩。
- `agentd/web/src/test/setup.ts`：Node ≥ 25 的实验性原生 `localStorage` 会盖掉 jsdom 实现并抛
  `localStorage.getItem is not a function`，整个前端套件在 Node 25 上全红。补一个内存版 Storage 兜底。

### 变更

- **CI 门禁覆盖**：backend job 从 4 个套件扩到全部 11 个（安全专项、配方、健壮性/性能泄漏、MCP、Telegram），
  新增「部署链路自检」步骤（无令牌 `--lan` 必须拒启 / `AGENT_TOKEN` 引导生效 / `--clear-token` 可恢复）。
- **文档纠偏**：`QUICKSTART.md`、`termux/install.sh`、`CONTRIBUTING.md` 里残留的旧仓库名 `termux-agent`
  改为 `pocketmind`；`DEPLOY.md` 说明令牌为打码显示、`--clear-token` 恢复方式与 `AGENT_TOKEN` 的首次语义；
  `web/start.sh` 的 Python 版本口径统一为 3.11+，`--lan` 提示改为"无令牌会拒绝启动"；
  `.monkeycode/MEMORY.md` 整体重写（原文描述的是已废弃的 goose/Rust/ACP 架构，对现在代码是纯误导）。
- 新增本 `CHANGELOG.md`，并在 `CONTRIBUTING.md` 发布流程中明确：主仓库在 GitCode，
  CI 跑在 GitHub 镜像，合并后必须 `git push github main --tags` 同步，否则门禁检查的是旧代码。
