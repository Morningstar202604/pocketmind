import { test } from "@playwright/test";
import path from "node:path";
import fs from "node:fs";
const OUT = path.resolve(process.cwd(), "readme-shots");
fs.mkdirSync(OUT, { recursive: true });

test("tool-call dark", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.waitForSelector("textarea");
  await page.locator("textarea").fill("运行一个命令");
  await page.locator("textarea").press("Enter");
  await page.waitForSelector(".approval", { timeout: 20000 });
  await page.mouse.move(2, 2);
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, "tool-call.png") });
});

test("settings light", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.goto("/");
  await page.waitForSelector("textarea");
  await page.getByRole("button", { name: "设置" }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT, "settings.png") });
});

test("recipes light", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await page.waitForSelector("textarea");
  await page.getByRole("button", { name: "定时任务" }).click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: path.join(OUT, "recipes.png") });
});

test("mobile 390", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.waitForSelector("textarea");
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, "mobile.png") });
});

test("capability matrix", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.goto("/");
  await page.waitForSelector("textarea");
  await page.getByRole("button", { name: "设置" }).click();
  await page.waitForTimeout(2500);
  const caps = page.locator(".caps-head");
  if (await caps.count()) { await caps.click(); await page.waitForTimeout(500); }
  await page.mouse.move(2, 2);
  await page.screenshot({ path: path.join(OUT, "capabilities.png") });
});
