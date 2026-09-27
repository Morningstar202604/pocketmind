# 口袋 Agent · Web 前端

React 18 + Vite + TypeScript + Zustand + fetch-event-source。
生产产物 `dist/` 直接入库（手机端零 Node 构建），由后端 `agentd` 托管。

## 开发

```bash
npm install
npm run dev        # Vite 开发服务器，/api 代理到 127.0.0.1:8787
npm run build      # tsc + vite，产物输出到 dist/
npm run test       # Vitest 单元测试
```

后端 mock 模式（不消耗 LLM 额度）：

```bash
python3 -m agentd.main --mock --port 8799   # 仓库根目录执行
```

## E2E（Playwright）

```bash
npm install -D @playwright/test
npx playwright install chromium   # 首次安装浏览器（CI 用 --with-deps）
npm run test:e2e                  # 自动拉起 mock 后端并跑 e2e/ 下用例
```

- 配置见 `playwright.config.ts`：`webServer` 自动执行 `python -m agentd.main --mock --port 8799`，
  并把 `AGENT_HOME` 指向 `/tmp/agentd-e2e`（隔离数据）。
- 沙箱/离线环境装不了浏览器时：脚本与配置已就绪，在 CI 中以手动触发（`workflow_dispatch`）方式运行即可。
- 本地若 `PLAYWRIGHT_BROWSERS_PATH` 指向旧目录，可临时覆盖：
  `PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright npm run test:e2e`。

## 主要目录

- `src/api.ts`：REST + SSE 客户端
- `src/store/useAgentStore.ts`：Zustand 全局状态（会话/消息/设置/审批/排队/Plan-Act）
- `src/components/`：Composer / MessageItem / PlanCard / SettingsPanel / TimerPanel / SessionList
- `e2e/`：Playwright 端到端用例
