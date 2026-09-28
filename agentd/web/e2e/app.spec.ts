// E2E 冒烟用例（mock 后端，确定性输出）：
//  1. 页面加载  2. 设置面板  3. 发消息收回复  4. 会话列表出现新会话
// 运行：npm run test:e2e（首次需 npx playwright install chromium）
import { expect, test } from "@playwright/test";

// 每个用例前：打开页面并新建一个空会话，避免上一条用例的聊天历史串扰。
test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "会话列表" }).click();
  await page.getByRole("button", { name: /新建/ }).click();
});

test.describe("口袋智灵 Web 端到端", () => {
  test("1. 页面加载：品牌与主界面可见", async ({ page }) => {
    // 顶栏 h1（exact 避免匹配到历史回复里的同名 markdown 标题）
    await expect(page.getByRole("heading", { name: "口袋智灵", exact: true })).toBeVisible();
    await expect(page.locator("textarea")).toBeVisible();
  });

  test("2. 设置面板：打开后可见 LLM 配置、权限模式与 Plan/Act 切换", async ({ page }) => {
    await page.getByRole("button", { name: "设置" }).click();
    await expect(page.getByText("模型厂商")).toBeVisible();
    await expect(page.getByText("工具权限")).toBeVisible();
    // P2：智能体工作方式（先计划 / 直接执行）
    await expect(page.getByText("工作方式")).toBeVisible();
    await expect(page.getByText("先计划", { exact: true })).toBeVisible();
    await expect(page.getByText("直接执行", { exact: true })).toBeVisible();
  });

  test("3. mock 模式下发「你好」收到 SSE 回复", async ({ page }) => {
    const box = page.locator("textarea");
    await box.fill("你好");
    await box.press("Enter");
    // 取最后一条 assistant 行（忽略历史），mock 后端回：（mock 模式）收到：你好…
    await expect(page.locator(".row.agent").last()).toContainText("收到：你好", {
      timeout: 15_000,
    });
  });

  test("4. 发送消息后会话列表出现新会话", async ({ page }) => {
    await page.locator("textarea").fill("你好");
    await page.locator("textarea").press("Enter");
    await expect(page.locator(".row.agent").last()).toContainText("收到：你好", {
      timeout: 15_000,
    });
    // 打开会话抽屉，至少有一条会话项
    await page.getByRole("button", { name: "会话列表" }).click();
    await expect(page.locator(".session").first()).toBeVisible();
  });
});
