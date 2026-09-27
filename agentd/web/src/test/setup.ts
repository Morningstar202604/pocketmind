// Vitest 全局 setup：引入 jest-dom 匹配器 + 浏览器 API polyfill。
import "@testing-library/jest-dom/vitest";

// jsdom 未实现 matchMedia，主题逻辑会用到，这里补一个最小可用版本
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
