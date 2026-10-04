// Vitest 全局 setup：引入 jest-dom 匹配器 + 浏览器 API polyfill。
import "@testing-library/jest-dom/vitest";

// Node ≥ 25 会在 globalThis 上注入实验性原生 localStorage（需要 --localstorage-file
// 才真正可用），它会盖掉 jsdom 的实现并让 api.ts 的 getToken() 抛
// "localStorage.getItem is not a function"。这里显式装一个内存版 Storage 兜底，
// 使测试在任意 Node 版本（本机 25 / CI 22）行为一致。
class MemoryStorage implements Storage {
  private map = new Map<string, string>();

  get length(): number {
    return this.map.size;
  }

  clear(): void {
    this.map.clear();
  }

  getItem(key: string): string | null {
    return this.map.has(key) ? (this.map.get(key) as string) : null;
  }

  key(index: number): string | null {
    return Array.from(this.map.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.map.delete(key);
  }

  setItem(key: string, value: string): void {
    this.map.set(key, String(value));
  }
}

if (typeof window.localStorage?.getItem !== "function") {
  const shim = new MemoryStorage();
  for (const target of [window, globalThis] as unknown as object[]) {
    Object.defineProperty(target, "localStorage", {
      value: shim,
      configurable: true,
      writable: true,
    });
  }
}
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
