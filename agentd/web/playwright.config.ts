// Playwright E2E 配置：
// - 浏览器直接打 agentd 托管的页面（baseURL=127.0.0.1:8799）
// - webServer 自动拉起 mock 后端（python -m agentd.main --mock），测试结束自动关闭
// - 沙箱/本地若未装浏览器：先 `npx playwright install chromium`（CI 用 --with-deps）
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

// webServer 需要从仓库根目录跑 `python -m agentd.main`（相对 web/ 向上两级）
// 注意：本工程 package.json 为 "type": "module"，不能用 __dirname
const here = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = path.resolve(here, "../..");

export default defineConfig({
  testDir: path.resolve(here, "e2e"),
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:8799",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // mock 模式后端：不消耗 LLM 额度，发"你好"会回固定文案，适合确定性 E2E
    command: "python3 -m agentd.main --mock --port 8799",
    cwd: repoRoot,
    url: "http://127.0.0.1:8799/api/health",
    timeout: 60_000,
    reuseExistingServer: !process.env.CI,
    env: {
      // AGENT_HOME 指向隔离目录，避免污染真实数据；
      // PATH 前置 faketermux 桩（CI 由 tests/setup_faketermux.sh 生成到 /tmp/faketermux）
      AGENT_HOME: process.env.AGENT_HOME || "/tmp/agentd-e2e",
      PATH: `/tmp/faketermux:${process.env.PATH || ""}`,
    },
  },
});
