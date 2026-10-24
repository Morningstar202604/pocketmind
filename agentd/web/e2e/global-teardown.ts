// E2E 全局清理：删掉本轮 mkdtemp 出来的数据目录。
//
// 为什么需要：playwright.config.ts 为了隔离用 mkdtempSync 每次新建一个
// AGENT_HOME，但 Playwright 不会替你删它 —— 每跑一次 E2E 就在系统临时目录
// 留一份 agent.db / checkpoints.db / jobs.db。本机实测跑了十几轮之后残留了
// 十几个目录。这里只在「目录是本轮自动创建」时删除；显式传入
// E2E_AGENT_HOME / E2E_HOME 的情况属于用户自己的目录，不动。
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

export default function globalTeardown(): void {
  const explicit = process.env.E2E_AGENT_HOME || process.env.E2E_HOME;
  if (explicit) return;
  const root = os.tmpdir();
  let entries: string[] = [];
  try {
    entries = fs.readdirSync(root);
  } catch {
    return;
  }
  for (const name of entries) {
    if (!name.startsWith("agentd-e2e-")) continue;
    const dir = path.join(root, name);
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch {
      /* Windows 上后端可能仍持有句柄：留到下次，不阻塞测试结果 */
    }
  }
}
