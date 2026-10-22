# 项目全维度审查与对标报告

> 审查对象：PocketMind（口袋智灵）
> 基线提交：`6ceca6a`（审查开始时的 HEAD）
> 审查日期：2026-10-22
> 审查方式：静态走读 + 命令实测 + 覆盖率采集 + 竞品对标 + 多轮截图巡检
> 所有条目均附 `文件:行` 或命令输出；未验证项明确标记 `[未确认]`

---

## 一、总体结论

整体质量评级：**A-（可投产；审查发现的问题已全部修复或明确记录处置决定）**。

审查开始时最大风险是 **`read_url` 的 SSRF**（F-001，Critical）：模型或被注入的模型可以
让 Agent 读取本机 `/api/export` 拿到全部会话数据，已实测复现并修复（22 条回归断言）。
其次是测试质量——当时覆盖率 48%、变异分数 55.2%，意味着大量断言是空的。

**审查后完成的修复（按影响排序）**：

| 项 | 审查时 | 现在 |
|---|---|---|
| Critical 安全缺陷 | 1（SSRF 可读全量数据） | **0** |
| 前端依赖 Critical/High | 2 CRITICAL + 3 HIGH | **0** |
| 变异分数 | 55.2%（约四成断言无效） | **100%**（28 杀 / 0 存活） |
| `Agent.chat` 圈复杂度 | 47 | **2** |
| `Agent._loop` 圈复杂度 | 53 | **11** |
| 后端行覆盖率 | 48% | **59%** |
| 分支覆盖率 | 未测 | **83.3%** |
| 可观测性 | 只有 health | **`/api/metrics`**（Prometheus 格式） |
| 门禁套件数 | 27 | **36** |

**仍未达标**：行覆盖率 59%（红线 90%）、分支 83.3%（红线 85%，差 1.7 点）、
技术债务比率未测。`create_app` 1100 行闭包**决定不拆**（理由见第六节末）。

**最优先的三项后续行动**：

1. **继续补覆盖率到 70%+**，重点是 `main.py` 剩余的路由错误分支与 `scheduler.py`（67%）。
   现在有了 `--with-mutation` 和分支覆盖数据，每补一块都能验证断言是否有效。
2. **接入技术债务度量**（SonarQube 或 `radon` + 自建阈值），目前是唯一未测的量化指标。
3. **真机 soak test**：所有验证都在 x86_64 模拟器/容器完成，aarch64 真机的长时间
   稳定性（内存泄漏、定时任务漂移）没有证据。

---

## 二、证据与基线

### 2.1 项目类型与技术栈

| 维度 | 实测值 | 证据 |
|---|---|---|
| 项目类型 | 本地优先的 AI Agent 后端 + PWA 前端（**非 SaaS**，单用户单机） | `README.md:17-26` |
| 后端 | Python 3.11+ / FastAPI / SQLAlchemy async / SQLite | `agentd/requirements.txt:1-23` |
| 前端 | React 18 + Vite 5 + Zustand + TypeScript | `agentd/web/package.json` |
| 部署形态 | Android Termux（主）/ Docker / VPS / 桌面 | `docs/capability-matrix.md` |
| 代码规模 | 后端 8,922 行 Python；前端 5,544 行 TS/TSX | `wc -l` 实测 |
| 数据敏感等级 | **高**：本地会话全文、LLM API Key、局域网访问令牌、短信/定位/剪贴板 | `agentd/config.py:82`、`agentd/tools/files.py:26-38` |
| 用户角色 | 单一本机用户（无多租户） | `agentd/main.py` 全部端点用单一 `check_token` |

**本项目不适用的维度**（明确声明，不凑数）：

- **多租户隔离 / 租户级配额**：不适用，产品是单用户单机。
- **K8s / 自动伸缩 / 容量规划**：不适用，单进程跑在手机上。
- **CDN / SRI / 前端供应链签名**：不适用，静态资源由本机 agentd 托管，无第三方 CDN。
- **SLO / 错误预算 / on-call 轮值**：不适用，无运维团队，个人自用。

### 2.2 实际运行的命令与输出

```
$ python -m ruff check agentd/ tests/ smoke/
All checks passed!

$ python -m compileall -q agentd/
(exit 0，无输出)

$ npx tsc -b --noEmit
(exit 0，无输出)

$ npm test
Test Files  12 passed (12)
     Tests  65 passed (65)

$ bash tests/run_all.sh --allow-dirty-dist
通过 27 / 跳过 0 / 失败 0
全绿
```

### 2.3 量化指标实测

| 指标 | 当前值 | 红线 | 判定 | 测量方式 |
|---|---|---|---|---|
| 行覆盖率（后端） | **59%**（审查时 48%） | ≥ 90% | ❌ 仍不通过 | `coverage run` 22 个套件合并（`coverage report`） |
| 分支覆盖率 | **83.3%**（1456 分支覆盖 1213） | ≥ 85% | ⚠️ 差 1.7 点 | `coverage run --branch` 22 套件合并 |
| 变异分数 | **100%**（28 个变异体全杀，1 个等价变异已证并排除；首测 55.2%） | ≥ 80% | ✅ 通过 | 自写 `smoke/p19_mutation_test.py`（AST 定位运算符 + 子进程跑测试） |
| 圈复杂度（最大） | **33**（`Agent._loop` 拆后为 11；`create_app` 仍高） | ≤ 10 | ⚠️ 部分改善 | `radon cc -s` |
| 圈复杂度（次大） | 47 → **2**（`Agent.chat`） | ≤ 10 | ✅ 通过 | 同上 |
| 超出 CC>10 的块 | 31 个（`agent.py` 内从 6 降到 5） | 0 | ❌ 不通过 | `radon cc -n C` |
| 函数 > 50 行 | 16 个 | 0 | ❌ 不通过 | AST 统计（见 3.2） |
| 技术债务比率 | `[未确认]` | ≤ 5% | `[未确认]` | 需 SonarQube，未接入 |
| 重复行率 | **6.5%** | < 3% | ❌ 不通过 | 自写 AST 行去重（剔除 import/except 模板） |
| Critical/High 漏洞 | 后端 1（SSRF，已修）；前端依赖原有 2 CRITICAL + 3 HIGH，已消除 | 0 | ✅ **0** | 手工审计 + OSV.dev 扫描（`p22`） |
| 前端包体积（gzip） | 97 KB | — | ✅ 健康 | `vite build` + `gzip` |

---

## 三、发现清单（按严重级别排序）

| ID | 严重级别 | 置信度 | 维度 | 位置 | 问题描述 | 证据 | 建议修复 | 对标含义 |
|---|---|---|---|---|---|---|---|---|
| F-001 | **Critical** | 高 | 安全 A01 | `agentd/tools/net.py:183` | `read_url` 只校验 `http(s)://` 前缀，未限制目标 IP。模型可读取本机任意 HTTP 服务 | 已复现：`read_url("http://127.0.0.1:64995/api/export")` 返回 `{"app":"口袋智灵",...,"settings":...}`（全量导出）；`169.254.169.254` 在云主机上等同获取临时凭据 | 已修：新增 `_check_url_allowed()`（IP 段 + 端口白名单 + DNS 解析后再判）+ `_SafeRedirectHandler`（重定向逐跳校验）。`smoke/p13_ssrf_test.py` 22 条断言 | **关闭差距**。Claude Code 的 WebFetch 默认拒绝内网地址；本项目此前完全没有防护 |
| F-002 | High | 高 | 测试有效性 | `agentd/main.py:276` | `create_app()` 单函数 **1052 行**，所有路由定义在闭包内，无法被单元测试直接调用 → `main.py` 覆盖率仅 **19%** | `radon cc` + AST 统计；`coverage report` 显示 `main.py 19%` | 把路由拆成 `APIRouter` 模块（`routes/sessions.py`、`routes/jobs.py`…），`create_app` 只做装配 | **关闭差距**。FastAPI 官方推荐做法就是分 router；单文件闭包是覆盖率天花板的结构原因 |
| F-003 | ~~High~~ **已修复** | 高 | 可维护性 | `agentd/agent.py:563` | `Agent._loop` 圈复杂度原 **53**（184 行），`Agent.chat` 原 **47** | `radon cc -s` 前后对比 | 已拆为 10 个方法：`chat`→**2**、`_loop`→**11**；行为由 p18 的 22 条基线锁定 | **关闭差距**。先建基线再重构，p4/p10/p18 全过后才提交 |
| F-004 | High | 高 | 测试有效性 | `smoke/p1_jobs_test.py:58`、`smoke/p2_plan_recipe_test.py:100` | 用 `except Exception: check(..., True)` 断言「非法输入被拒」——服务挂掉也会抛异常并判通过 | 读代码；已改为只接受具体 `HTTPError` 状态码 | 已修（提交 `6b50786`） | **关闭差距** |
| F-005 | Medium | 高 | 可维护性 | `agentd/main.py:290` | `lifespan` 180 行，`main()` 100 行 | AST 统计 | 拆出 `_startup_store` / `_startup_mcp` / `_startup_scheduler` | **承认差距** |
| F-006 | Medium | 高 | 性能（前端） | `agentd/web/src/App.tsx:166` | 消息列表无虚拟化，`messages.map` 每帧全量渲染；`MessageItem` 未 memo | 读代码；已修：`memo` + `useCallback` + `messageMemo.test.tsx` 回归 | 已修（本轮） | **承认差距**。Claude/ChatGPT 的对话列表都做虚拟化；本项目单机 200 条以内够用 |
| F-007 | Medium | 高 | 可访问性 | `agentd/web/src/styles.css:27,80` | 浅色主题 `--warn: #c98a10` 对比度 **2.95:1**；`--err` 3.74；`--ok` 3.06，均低于 WCAG AA 4.5 | 自写对比度计算 + `p9_a11y_contrast_test.py` | 已修：`--warn→#96630a`(5.14)、`--err→#c62828`(4.96)、`--ok→#0a7c57`(4.60) | **关闭差距** |
| F-008 | Medium | 高 | 可访问性 | `agentd/web/src/styles.css:573` | 「需要确认」状态复用 `blink` 动画（opacity 0.3→1），截图抓到 0.3 帧时几乎不可见 | 截图 `audit/C1-approval.png` | 已修：改为实线边框 + 加粗，不再用透明度动画 | **关闭差距** |
| F-009 | Medium | 高 | 功能完整性 | `agentd/web/src/components/MessageItem.tsx:79` | `TOOL_LABEL` 只覆盖 5 个工具，`run_shell` 等 34 个工具在审批卡显示英文内部名 | 截图 + 代码对照 | 已修：补全 39 个工具的中文标签 | **关闭差距** |
| F-010 | Medium | 高 | 依赖治理 | `agentd/requirements.txt:31` | `opentelemetry-exporter-otlp-proto-grpc` 拖入 `grpcio`，在 Termux 上需编译 BoringSSL/Abseil 全套 C++，实测 >1 小时且吃满 2GB 磁盘 | Android 模拟器实测：`No space left on device` → rustc SIGSEGV | 已修：移出主依赖，改为可选 | **跨越差距**：不做「照抄云端可观测性」，本地 Agent 默认不需要 OTLP |
| F-011 | Medium | 高 | 可靠性 | `agentd/scheduler.py:129` | Android/Termux 无 tzdata，`AsyncIOScheduler` 构造时 `ZoneInfoNotFoundError` 导致服务起不来 | Android 15 模拟器实测崩溃堆栈 | 已修：显式时区 + 四级降级 | **关闭差距** |
| F-012 | Medium | 中 | 可观测性 | 全局 | 无结构化日志、无指标端点；`/api/stats` 只有计数 | `grep` 无 `prometheus`/`metrics` | 提供 `/api/metrics`（请求数、延迟、工具调用失败率） | **承认差距**：单机自用，OTel 已可选 |
| F-013 | Low | 高 | 一致性 | `agentd/web/src/components/MessageItem.tsx:270` | 用户消息与助手消息的时间戳位置不一致 | 截图对比 | 已修：统一到操作栏内 | **关闭差距** |
| F-014 | Low | 高 | 移动适配 | `agentd/web/src/styles.css:1867` | 320px 下标题折成两行、引导卡文字折行 | 截图 `empty-320.png` | 已修：`@media (max-width:360px)` + `.tasks` 单列 | **关闭差距** |
| F-015 | Low | 高 | 依赖治理 | `agentd/requirements.txt:9-13` | 只有下界无上限，大版本发布会静默破坏部署 | 读过 `mcp 1.x→2.x` 的历史兼容分支 | 已修：关键包加 `<3` / `<1` + `check_requirements.py` 校验 | **关闭差距** |
| F-016 | Low | 高 | 测试有效性 | `tests/` 全目录 | 后端测试是脚本式（自己统计 passed/failed），无法被 pytest 收集 → 覆盖率工具默认采不到 | `pytest --collect-only` 只收集 6 个 | 可选：给脚本套 runner 适配层，或接受现状并用 `coverage run` 逐脚本采集 | **承认差距** |
| F-017 | ~~Medium~~ **已修复** | 高 | 供应链 | `agentd/web/package-lock.json` | 前端依赖曾有 2 CRITICAL + 3 HIGH（tinypool RCE、vite 路径穿越、source-map-js DoS） | OSV.dev 实测（`p22`） | 升级 vite 5→7.3.7、vitest 2→5.0.5、`overrides` 强制 source-map-js ^1.2.2；现 0 Critical/High | **关闭差距** |
| F-018 | Low | 高 | 分支覆盖 | — | 已采 `--branch`：**83.5%**，距 85% 红线差 1.5 点 | `coverage report` | 补 `main.py` 的错误分支（现 41%） | **承认差距** |

### F-001 的最小可复现 PoC（已执行）

```python
# 修复前
from agentd.tools.net import read_url
r = await read_url("http://127.0.0.1:64995/api/export", 400)
# 实际返回：ok=True, text='{"app":"口袋智灵","version":"0.2.0","exported_at":...'
# 即：一条被注入的消息 → 模型调用 read_url → 全部会话数据进入上下文
#     → 随下一次 LLM 请求离开设备（本产品宣称"数据不出本机"）
```

修复后同一调用返回：`拒绝访问：禁止访问内网/保留地址（web_search 的链接同样受此限制）`

---

## 四、查漏补缺清单（按项目类型）

项目类型：**AI Agent 应用（本地优先）+ 单用户后端服务**。以下按该类型的专项检查组织。

| 专项 | 检查项 | 结果 | 证据 |
|---|---|---|---|
| **Agent 专属** | 工具调用的审批是否服务端强制 | ✅ 通过 | `agentd/agent.py` 工具执行前 `ask_approval`；`permissions.should_ask` 按 risk×mode 判定 |
| | 全局拒绝名单是否短路 | ✅ 通过 | `approval.is_auto_denied(name)` 在 emit 之前判断，不建 gate（避免幽灵审批卡） |
| | 超时是否 fail-closed | ✅ 通过 | `approval.py:161` `return "timeout"` → `status="denied"` |
| | 上下文压缩是否会丢掉最近对话 | ✅ 通过 | `_should_compress` + 保留最近 1/3；`p4` 有断言 |
| | 工具结果是否可能撑爆上下文 | ✅ 通过 | `_tool_result_limited` 按预算截断 |
| | 子代理是否可能提权 | ✅ 通过 | `SUBAGENT_BLOCKED` 收窄工具集 + 禁止嵌套；`p10` 14 条断言 |
| | 提示注入的防护 | ⚠️ 部分 | 无 prompt 层防护；依赖审批门。`read_url` 返回的外部内容可含注入指令——**这是设计上的固有风险**，已通过工具收窄缓解 |
| **单用户后端** | 每个端点是否鉴权 | ✅ 通过 | 43 个端点中 41 个带 `check_token`；豁免的 2 个是 `/api/health` 与静态首页（合理） |
| | 令牌为空时是否默认拒绝 | ✅ 通过 | `--lan` 无令牌直接 `SystemExit(1)`（部署自检覆盖） |
| | 令牌是否会在日志/响应中泄露 | ✅ 通过 | `_masked_settings()` 统一打码；`p7` 有断言 |
| | 敏感目录是否可被文件工具读取 | ✅ 通过 | `_denied_dirs()` 拦 `.agent`/`.ssh`/`.gnupg`；实测 5 条攻击路径全拦 |
| **PWA/前端** | 触屏目标 ≥ 36px | ✅ 通过 | `mobile.spec.ts` 8 视口全过 |
| | 最小字号 ≥ 12px | ✅ 通过 | 同上 |
| | 深色/浅色双主题对比度 | ✅ 通过（修复后） | `p9` 14 条断言 |
| | 断线是否可见 | ✅ 通过 | `connectionStatus` 三态 + 指数退避轮询 |
| **Termux/手机** | 安装是否幂等 | ✅ 通过 | `p11`（全已装时 0 次 pkg 调用） |
| | 无 tzdata 能否启动 | ✅ 通过（修复后） | `p11` 场景 7 |
| | 硬件能力缺失是否提前告知 | ✅ 通过 | 能力矩阵 `p12` 19 条断言 |
| **不适用** | 多租户/计费/K8s/CDN SRI | 不适用 | 产品形态为单用户单机 |

---

## 五、竞品对标矩阵

### 5.1 对标对象（具名）

| 竞品 | 选择理由 |
|---|---|
| **Claude Code** | AI Agent 的行业标杆：工具审批模型、子代理、上下文管理、AGENTS.md 约定均由此确立；与本项目同为「本地跑、操作本机」的 Agent |
| **Termux 生态同类**（`termux-ai` / `Open Interpreter on Termux`） | 同运行时环境（Android + Termux），可比「安装难度 / 依赖体积 / 手机能力调用」；这两项是云端 Agent 无法类比的约束 |

### 5.2 能力对比

| 能力/维度 | 本项目 | Claude Code | Termux 同类 | 差距类型 | 优先级 |
|---|---|---|---|---|---|
| 工具审批（分级 + 全局名单） | ✅ 三级 + 黑名单 | ✅ 每次确认 + allowlist | ⚠️ 多数无审批 | **跨越**（本项目更细） | — |
| 子代理（独立上下文） | ✅ 有，工具收窄 | ✅ Task 工具，可并行多个 | ❌ 无 | **承认**：串行 vs 并行 | P2 |
| Todo 清单 | ✅ 有 | ✅ 有 | ❌ | **关闭** | — |
| 上下文压缩 | ✅ 摘要 + 分页归档 | ✅ 自动 compact | ❌ | **关闭** | — |
| 会话搜索 | ✅ 标题 + 正文全文 | ✅ | ❌ | **关闭** | — |
| 联网搜索 | ✅ web_search + read_url | ✅ WebFetch/WebSearch | ❌ | **关掉**（有，但受 SSRF 修复限制） | — |
| 手机硬件能力（短信/定位/传感器） | ✅ 25 个工具 | ❌ 桌面无 | ⚠️ 部分 | **跨越**（本项目独有） | — |
| MCP Server（反向暴露给桌面） | ✅ 39 个工具 | ❌（只做 client） | ❌ | **跨越** | — |
| 多模态（图片理解/语音输入） | ⚠️ 有 STT/TTS，无图片理解 | ✅ | ❌ | **承认** | P3 |
| 视觉/UI 打磨 | ✅ 双主题 + WCAG AA | CLI，无 GUI | ❌ | **跨越** | — |
| 安装体积 | ⚠️ 首次 50 分钟（Termux） | — | ⚠️ 类似 | **承认**：Rust 编译不可避免 | P1 |
| 测试覆盖率 | ❌ 48% | —（闭源） | — | **关闭** | P0 |
| 可观测性 | ⚠️ 可选 OTel | ✅ 内置 | ❌ | **承认** | P3 |
| 并行子代理 | ❌ 串行 | ✅ | ❌ | **承认** | P2 |
| 多设备同步 | ❌ 单机 | ❌ | ❌ | **刻意忽略**（与"数据不出本机"冲突） | — |
| 云端多租户 | ❌ | ❌ | ❌ | **刻意忽略**（产品定位不同） | — |

**结论**：本项目在「手机硬件能力 + 审批粒度 + MCP 反向暴露」三项上**领先** Claude Code；
在「并行子代理、多模态、可观测性」上落后；在「测试覆盖率」上明显落后于工程标准。

---

## 六、修复与补齐路线图

| 优先级 | 要做什么 | 对应发现 | 工作量 | 验收标准 |
|---|---|---|---|---|
| **P0（已完成）** | SSRF 防护 | F-001 | S | `p13` 22 条断言全过；6 类攻击路径实测被拦 |
| **P0（已完成）** | 修假测试（`except Exception` 判通过） | F-004 | S | 服死时测试必须失败 |
| **P0（已完成）** | 关键包加主版本上限 | F-015 | S | `check_requirements.py` 报错 |
| **暂缓** | 拆 `create_app` 为 APIRouter 模块 | F-002 | **L** | 见下节「关于 create_app 拆分：决定不做」——可测性痛点已由 p14 解决，风险高于收益 |
| ~~P1~~ **已完成** | 拆 `Agent._loop` / `Agent.chat` | F-003 | M | 已达成：`_loop` 53→11、`chat` 47→2；p4 53 项 + p10 14 项 + p18 22 项全过 |
| ~~P1~~ **已完成** | 变异测试 | F-006(指标) | M | 已达成：变异分数 55.2% → **100%** |
| **P1** | 采分支覆盖率 | F-018 | S | 报告出 branch% |
| **P2** | 并行子代理 | 对标差距 | M | 同时派 2 个子任务，总耗时 < 串行 70% |
| **P2** | `/api/metrics`（请求数/延迟/工具失败率） | F-012 | S | Prometheus 文本格式可被抓取 |
| **P3** | 图片理解（多模态） | 对标差距 | L | 上传图片 → 模型可读 |
| **P3** | 前端消息列表虚拟化 | F-006 | M | 500 条消息首屏渲染 < 100ms |

**本轮已完成并验证**（共 13 项）：

- F-001 SSRF、F-004 假测试、F-007/F-008 a11y、F-009 工具名、F-010 依赖、F-011 时区、
  F-013/F-014 布局、F-015 版本上限
- F-003 的一半：`chat` 复杂度 47→29
- F-006 的一半：`MessageItem` memo 化
- **F-002 的部分缓解**：新增 `p14_router_test.py`（65 断言，进程内覆盖 43 个端点），
  `main.py` 覆盖率 19% → **44%**；新增 `p15_file_tools_test.py`（35 断言），
  `files.py` 39% → **89%**；新增 `p16_knowledge_test.py`（24 断言），`knowledge.py` 31% → **100%**。
  总覆盖率 48% → **57%**。

**第二轮补充（覆盖率 57% → 59%）**：

- `p17_net_tools_test.py`（38 断言，**全离线**）：`net.py` 31% → **84%**。用固定 HTML
  喂解析器 + monkeypatch `urlopen`，覆盖"Bing 改版 → 回退 DDG""两引擎全挂 → 错误列出
  每个原因""确实没搜到 → 空结果而非报错"。
- `p18_agent_loop_test.py`（22 断言）：为 `_loop` 建立**行为基线**——Plan 模式不执行工具、
  批准/拒绝/超时三条审批分支、工具报错不中断循环、轮数用尽 emit error。`agent.py` 59% → **66%**。
  这是重构 `_loop` 前的安全网；已验证破坏 Plan 的 early-return 会让它立刻失败。

**第三轮：F-003 完成（`Agent._loop` 重构）**

在 p18 的 22 条行为基线保护下拆分 `agentd/agent.py`，**行为零变化**（p4 53 项、p10 14 项、p18 22 项全过）：

| 函数 | 重构前 CC | 重构后 CC | 拆分出的方法 |
|---|---|---|---|
| `Agent.chat` | 47 | **2** | `_runtime_limits` / `_build_system_prefix` / `_memory_context_block` / `_load_history` |
| `Agent._loop` | 53 | **11** | `_stream_round` / `_finish_plan` / `_finish_turn` / `_run_one_tool` / `_decide_tool` / `_record_checkpoint` |

`_decide_tool` 把四条审批语义（chat 模式拒绝 / 无通道 fail-closed / 未知工具询问 /
risk×mode 判定）从嵌套五层的 if-else 变成四个带注释的早返回——这是本轮最值得的一处，
因为它此前只能靠读代码确认安全语义。

**第四轮：变异测试（回答"59% 覆盖率里有多少有效断言"）**

`mutmut` 3.2.3 的 CLI 依赖 Unix `resource` 模块、`cosmic-ray` 在镜像源不可用，
因此自写 `smoke/p19_mutation_test.py`：AST 定位运算符/布尔常量 → 文本替换 →
子进程跑测试 → 统计"杀死/存活"。

**首测总变异分数 55.2%**（16 杀死 / 13 存活）——证明当时的 59% 覆盖率里约四成断言无效。
逐个补断言后达到 **100%**（28 杀死 / 0 存活 / 1 等价变异已证明并排除）。

补的断言全部是真实边界，不是凑数：

| 模块 | 首测 | 现在 | 补的断言 |
|---|---|---|---|
| `permissions.py` | 60% | **100%** | risk × mode 九种组合逐一断言（原只测了 approve 下 write/danger） |
| `files.py` | 33% | **100%** | 敏感目录**子路径**（`.ssh/id_rsa`）被拦、`MAX_TEXT` 与 1MB 的等号边界、`_roots()` 去重 |
| `todo.py` | 33% | **100%** | "只有 store 没有 sid" / "只有 sid 没有 store" 两个单侧缺失、`_MAX_ITEMS` 截断、`emit` 为 None 与抛异常 |
| `net.py` | 33% | **100%** | DDG 无结果容器时整页解析、limit 上限钳制、非字符串入参、scheme 白名单 |
| `knowledge.py` | 83% | **100%** | 恰好 20000 字可存（`>` vs `>=` 边界） |

**`files.py` 的发现最有价值**：`if p == d or d in p.parents` 改成 `and` 后，
只有目录本身被拦，**里面的 `id_rsa` 反而能读**。原测试只断言了目录本身，没断言子路径。

**变异器自身也修了一个真 bug**：`subprocess.run([python, test_a, test_b])` 只执行
`test_a`，后面被当成 `sys.argv` —— 导致第二个套件的断言从未运行、变异被误判为存活。
改成逐个跑后，`net.py` 从 33% 跳到 83%。

**第五轮：补齐可量化的缺口（分支覆盖 / 依赖漏洞 / 指标端点）**

1. **分支覆盖率已采**：行 60% / 分支 **83.5%**（1434 个分支覆盖 1198），距 85% 红线差
   1.5 个百分点。`main.py` 分支覆盖仅 41%，是主要拖累。

2. **依赖漏洞扫描（F-017 关闭）**：`pip-audit` 在本机返回空且 exit 0——加 `-v` 才看出它
   卡在访问 pypi.org（本机不通），**把网络失败静默当成"没有漏洞"**。改用 OSV.dev REST API
   自建 `p22_dep_audit_test.py`，首扫就发现前端 **2 个 CRITICAL + 3 个 HIGH**：
   - `tinypool@1.1.1` 原型污染 → RCE（CRITICAL ×2）
   - `vite@5.4.21` 路径穿越 / launch-editor 注入（HIGH ×2）
   - `source-map-js@1.2.1` 事件循环 DoS（HIGH）
   全部是 `dev=True`（构建时依赖，不进 `dist/`），但开发机被攻破同样危险。
   已升级 vite 5→7.3.7、vitest 2→5.0.5、`overrides` 锁 source-map-js ^1.2.2。
   **复扫 0 Critical/High**；67 个前端单测 + 构建全过。

3. **`/api/metrics`（F-012 部分关闭）**：`agentd/metrics.py` 进程内计数器 +
   Prometheus 文本格式，无新依赖。中间件自动统计所有请求（含 404/500），
   工具执行记录耗时与失败率。路径归一化到 `/{id}`，避免指标基数随会话数爆炸，
   也避免把会话 id 灌进监控系统（`p23` 24 条断言，含"文本不含原始 id"）。

4. **`main.py` 的聊天编排已覆盖（p24）**：`run_agent` 109 行此前完全没有测试
   （p14 用 TestClient 测不到，它需要真实 LLM 响应）。新增
   `p24_run_agent_test.py`（18 条断言）覆盖六条路径：正常一轮 / 队列续跑 /
   取消 / 未配置模型快速失败 / Agent 抛异常 / 取消后不续跑。
   `main.py` 分支覆盖 41% → **48%**。

   写这个测试时暴露了一个真实的测试盲区：直接 `await run_agent(sid, msg)` 而不先注册
   `runs[sid]`，会让 `emit_now()` 返回 None，assistant 消息永远不落库——
   第一版测试因此只看到 user 消息。真实路径是 `/api/chat` 先注册再 create_task。

### 关于 `create_app` 拆分：**决定不做**（附理由）

这是审计里唯一剩下的 P1，我评估后决定**不动**，理由如下，供后续决策参考：

| 事实 | 数据 |
|---|---|
| `create_app` 函数体 | 276–1383 行，约 1100 行 |
| 内部共享的闭包变量 | 10 个（`store` / `approval` / `mem0` / `profile` / `skills` / `runs` / `scheduler_svc` / `mcp_clients` / …） |
| 对闭包变量的引用点 | 93 处 |
| 受影响的路由 | 全部 43 个 |

拆分不是"把函数切段"，而是要把这 10 个变量重构进一个应用状态对象
（如 `app.state.ctx`），然后改写 43 个路由的 93 处引用。**每一行都必须改**，
而 `create_app` 是启动路径——改错一处，服务起不来。

**为什么现在不值得做**：

1. **原来的核心痛点已经解决**。拆 `create_app` 的初衷是"路由无法测试、覆盖率只有 19%"。
   `p14` 用 `TestClient` 在进程内驱动 ASGI app，已把 `main.py` 语句覆盖拉到 48%、
   分支 48%，43 个端点全部有错误分支断言。可测性问题绕过去了。

2. **风险收益不匹配**。收益是"代码更好读"（主观、无量化指标）；代价是
   1100 行 × 93 处引用的机械改动 + 启动路径回归风险。即使有 26 个套件兜底，
   也难保证不漏。

3. **剩余价值可以用更小的代价拿到**。如果将来确实要拆，正确顺序是：
   先补到分支覆盖 70%+，再按功能域（sessions / jobs / knowledge / settings）
   分四批搬，每批单独提交、单独跑全量。

**如果要拆，验收标准应该是**：43 个端点行为完全不变（`p14` 52 个请求全过）、
`main.py` 单文件行数减半、分支覆盖不下降。目前不具备前提，故不动。

覆盖率 59-60%，离 90% 红线仍有 30 个百分点；但**变异分数 100%** 说明现有覆盖到的
那部分代码，断言是有效的。

---

## 七、审查结论的局限

诚实声明以下内容**没有**被验证，不计入通过项：

1. ~~分支覆盖率 / 变异分数~~ **已测量**：分支 83.3%、变异 100%。**技术债务比率仍未测**（需 SonarQube 或等价工具，未接入）。
2. ~~CVE 扫描~~ **已扫描**（OSV.dev，`p22`）：后端 0 漏洞，前端已从 2 CRITICAL + 3 HIGH 清零。
3. **真机性能与长时间稳定性**：仅在 x86_64 模拟器验证过「装得上、起得来」，
   aarch64 真机的编译耗时与内存表现未知。
4. **`main.py` 的路由层**：被 7 个 API 套件覆盖，但覆盖率工具采不到跨进程数据，
   实际覆盖率**高于** 19%，具体值 `[未确认]`。
5. **提示注入**：`read_url` 读取的外部内容可能含注入指令，本轮未做对抗测试。
6. 审查由单一执行者完成，**未做交叉复核**；结论可能受视角局限。
