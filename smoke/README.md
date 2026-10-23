# 测试说明

## 运行前提

最省事的方式是从仓库根跑总门禁，它会把服务、桩和依赖都准备好：

```bash
bash tests/run_all.sh            # 后端全量 + 前端全量
bash tests/run_all.sh --backend  # 只跑后端（本目录的所有套件都包含在内）
```

单独跑某个套件时才需要自己准备环境：

- `python -m agentd.main --mock --port 8799`（AGENT_HOME 用持久路径，如 `~/.agent-test/pa-test`）。
  端口可用环境变量改成别的地址：`AGENTD_BASE=http://127.0.0.1:9000 python smoke/m5_regression.py`
- `/tmp/faketermux/` 25 个 termux 命令桩（**会被系统周期性清空**，出现"termux-api 未安装"时重建；
  桩需返回真实格式，如 battery 输出 JSON 数组 `[{"status":...}]`）；重建用 `bash tests/setup_faketermux.sh`（当前产出 23 个命令桩）

## 测试清单

| 脚本 | 覆盖 | 项数 |
|---|---|---|
| `m5_regression.py` | 安全加固回归：短 key 打码、DB/目录权限 600/700、电池链路（auto）、danger 审批+拒绝+收尾、未知工具审批、会话命名、工具清单 ≥38 项 | 11 |
| `p4_agent_depth_test.py` | P4 agent 深度：配置接线（链长/重试/检索条数/压缩阈值/FTS 批量）、中断恢复（孤立 tool_calls 回填）、三级审批（deny_always/allow_global/旧格式兼容）、MCP 注解分级、档案记忆注入、知识库显式检索、摘要链路（最旧 N 条不摘最近）、Skills 渐进披露、todo 工具 | 53 |
| `p5_frontend_contract_test.py` | P5 前端契约：导出覆盖档案/清单/知识库、agent/context/memory 三块设置回环与越界收敛、新工具分组齐备、手动压缩端点 | 18 |
| `p6_unconfigured_test.py` | P6 首次使用（未配置模型）：自起真实模式服务，断言 health 给出缺失项、发消息 5 秒内返回指引、连续两条都处理（不被永久锁成「排队」）、历史留完整一问一答 | 8 |
| `p7_cross_audit_test.py` | P7 横向审计修复回归：非 ASCII 令牌不 500、非法配置块不污染（否则设置页永久 500）、litellm/MCP env 密钥不回传、interval 极值 400、get_messages 取最近 N 条、run_shell 非零退出带 error、phone 写操作校验退出码、凭据目录不可读、MCP readOnlyHint 不降级、取消后不续跑、清空任务清单有服务端端点（刷新不复活）、delete_session 失败回滚、配置回退不绕范围收敛 | 49 |
| `p0_queue_test.py` | P0 排队验收：并发——第一轮挂审批时第二轮应 `queued`，审批放行后自动执行 | 3 |
| `p1_jobs_test.py` | P1 定时任务验收：创建 interval 任务 → 触发执行 → 启停 → 条件拒绝 → 删除 → jobs.db 持久化恢复 | 8 |

## 单测链路（checkpoint/undo）

写文件覆盖 → 结果带 `_ckpt` → 落 CheckpointStore → `undo` 还原内容；
删文件 → `_ckpt` → `undo` 找回文件。参见源码 `agentd/checkpoints.py` 与
`agentd/tools/files.py`（执行前自动 `backup_file`）。

## 浏览器实测

- 设置面板工具分组（手机 25 / 文件 4 / 任务与编排 3 / 知识库 3 / 长期记忆 1 / 技能 1 / 系统 1 / 语音 1 / 联网 2）
- 能力面：运行参数与上下文/记忆旋钮可调并保存；知识库条目可删（二次确认）；技能只读展示
- 品牌细节：顶栏状态灯（绿/琥珀/红）、任务清单进度条、工具结果复制按钮、空态新文案
- 手机适配矩阵（e2e/mobile.spec.ts，8 视口）：触摸目标 ≥36px、不依赖 hover、审批 5 键完整可见、无溢出/裁切
- 注意：该 e2e 用独立端口 8793 与独立数据目录，并在 beforeAll 里把 permission_mode 设回 approve——
  否则会复用 m5/p2 改过权限模式的实例，导致审批不弹出而全线误报
- 定时任务面板：新建「每 60 秒」任务 → 列表显示下次运行时间 → 到点执行
- 会话列表重命名按钮（✎）；P0 排队横幅；审批卡展开思考链
- 截图存 `docs/history/screenshots-demo/`（历史快照）
