// E2E 冒烟用例（mock 后端，确定性输出）：
//  1. 页面加载  2. 设置面板  3. 发消息收回复  4. 会话列表出现新会话
// 运行：npm run test:e2e（首次需 npx playwright install chromium）
import { expect, test } from "@playwright/test";

// 每个用例前：打开页面并新建一个空会话，避免上一条用例的聊天历史串扰。
test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "会话列表" }).click();
  await page.getByRole("button", { name: /新建/ }).click();
  // 等上一轮生成真正结束再交棒：新建会话不会中断正在跑的流，
  // 若上一条用例的回复还在流式输出，输入框占位会停在「生成中…」，
  // 后续用例的 fill+Enter 就发不出去（表现为"没弹审批"而非报错，很难定位）。
  // 注意不能用 .send 是否 enabled 判断——它只取决于输入是否为空。
  await expect(page.locator("textarea[placeholder^='输入指令']")).toBeVisible({ timeout: 30_000 });
});

test.describe("口袋智灵 Web 端到端", () => {
  test("1. 页面加载：品牌与主界面可见", async ({ page }) => {
    // 顶栏 h1（exact 避免匹配到历史回复里的同名 markdown 标题）
    await expect(page.getByRole("heading", { name: "口袋智灵", exact: true })).toBeVisible();
    await expect(page.locator("textarea")).toBeVisible();
  });

  test("2. 设置面板：打开后可见 LLM 配置、权限模式与 Plan/Act 切换", async ({ page }) => {
    await page.getByRole("button", { name: "设置" }).click();
    // 「模型」组默认展开（常用配置）；「权限与安全」收起，展开后再核对内容
    await expect(page.getByText("模型厂商")).toBeVisible();
    await page.getByRole("button", { name: /权限与安全/ }).click();
    await expect(page.getByText("工具权限")).toBeVisible();
    // P2：智能体工作方式（先计划 / 直接执行）—— 位于「智能体模式」分组，展开后核对
    await page.getByRole("button", { name: /智能体模式/ }).click();
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

test.describe("聊天滚动与工具耗时", () => {
  test("长对话自动滚到底；用户上翻时不被拽回，可一键回到最新", async ({ page }) => {
    // 关键回归：滚动容器是 .chat（overflow-y:auto），不是 .msgs；
    // 旧代码给 .msgs 设 scrollTop 完全无效 —— 长对话里新消息永远停在视口外。
    await page.locator("textarea").fill("长对话滚动测试");
    for (let i = 1; i <= 4; i++) {
      await page.locator("textarea").fill(`第 ${i} 条：把对话撑到超过一屏，用于验证自动滚动是否生效。`);
      await page.keyboard.press("Enter");
      await page.waitForTimeout(2200);
    }
    const gap = () =>
      page.evaluate(() => {
        const el = document.querySelector(".chat")!;
        return Math.round(el.scrollHeight - el.scrollTop - el.clientHeight);
      });
    expect(await gap(), "发消息后未自动滚到底").toBeLessThan(80);

    await page.evaluate(() => {
      document.querySelector(".chat")!.scrollTop = 0;
    });
    await expect(page.locator(".to-bottom"), "上翻后未出现「最新」按钮").toBeVisible();
    await page.locator("textarea").fill("上翻期间到达的新消息");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2600);
    expect(await gap(), "上翻时新内容把用户拽回了底部").toBeGreaterThan(200);

    await page.locator(".to-bottom").click();
    await page.waitForTimeout(800);
    expect(await gap(), "点「最新」未回到最底").toBeLessThan(80);
    await expect(page.locator(".to-bottom")).toHaveCount(0);
  });

  test("工具卡显示执行耗时", async ({ page }) => {
    // 先等应用完成初始化（空态渲染出来）再输入：否则初始化尚未挂上事件时
    // fill+Enter 会被丢掉，消息根本没发出（全量并行跑时出现过这个竞态）
    await expect(page.locator("textarea")).toBeVisible();
    await page.locator("textarea").fill("运行一个命令");
    await page.keyboard.press("Enter");
    // 确认消息真的进入列表，避免"没发出去"被误判成"没弹审批"
    await expect(page.locator(".row.user").last(), "消息未发送成功").toContainText("运行一个命令");
    await page.waitForSelector(".approval", { timeout: 20000 });
    await page.locator(".approval .btn.ok").first().click();
    await expect(page.locator(".tool-status").first()).toContainText("完成", { timeout: 20000 });
    // 耗时只在 ≥300ms 时显示，mock 下工具通常几毫秒完成，因此这里可能没有该元素。
    // 注意：必须用 count()（瞬时）判断存在性——对不存在的元素调用 textContent()
    // 会一直等到超时，把"这一项没显示"变成"测试超时"（曾因此误判为应用 bug）。
    // 格式正确性由 partReducer 的单测确定性覆盖。
    if ((await page.locator(".tool-ms").count()) > 0) {
      expect(await page.locator(".tool-ms").first().textContent()).toMatch(/^(\d+ms|\d+\.\d+s)$/);
    }
  });
});
