# 测试说明

## 运行前提

- `python -m agentd.main --mock --port 8799`（AGENT_HOME 用持久路径，如 `~/.agent-test/pa-test`）
- `/tmp/faketermux/` 25 个 termux 命令桩（**会被系统周期性清空**，出现"termux-api 未安装"时重建；
  桩需返回真实格式，如 battery 输出 JSON 数组 `[{"status":...}]`）

## 测试清单

| 脚本 | 覆盖 | 项数 |
|---|---|---|
| `m5_regression.py` | 安全加固回归：短 key 打码、DB/目录权限 600/700、电池链路（auto）、danger 审批+拒绝+收尾、未知工具审批、会话命名、工具清单 31 项 | 10 |
| `p0_queue_test.py` | P0 排队验收：并发——第一轮挂审批时第二轮应 `queued`，审批放行后自动执行 | 3 |
| `p1_jobs_test.py` | P1 定时任务验收：创建 interval 任务 → 触发执行 → 启停 → 条件拒绝 → 删除 → jobs.db 持久化恢复 | 8 |
| `mcp_demo_server.py` | MCP 最小 demo 服务器（stdio JSON-RPC），配合验证 agentd 的 MCP 客户端互通 | — |

## 单测链路（checkpoint/undo）

写文件覆盖 → 结果带 `_ckpt` → 落 CheckpointStore → `undo` 还原内容；
删文件 → `_ckpt` → `undo` 找回文件。参见源码 `agentd/checkpoints.py` 与
`agentd/tools/files.py`（执行前自动 `backup_file`）。

## 浏览器实测

- 设置面板工具分组（手机 25 / 文件 4 / 系统 1 / 语音 1）
- 定时任务面板：新建「每 60 秒」任务 → 列表显示下次运行时间 → 到点执行
- 会话列表重命名按钮（✎）；P0 排队横幅；审批卡展开思考链
- 截图存 `demo-shots/01-09`
