/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vitest 配置：复用 Vite 的 react 插件，使用 jsdom 模拟浏览器环境。
// 测试入口在 src/**/*.test.ts(x)，setup 文件引入 @testing-library/jest-dom 匹配器。
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // e2e/ 是 Playwright 用例（另跑 npm run test:e2e），不要被 Vitest 收进来
    exclude: ["e2e/**", "node_modules/**", "dist/**", "playwright.config.ts"],
  },
});
