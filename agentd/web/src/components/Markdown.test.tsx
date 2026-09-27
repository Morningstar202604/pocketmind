// Markdown 渲染安全测试：DOMPurify 必须剥掉 LLM 输出里的 XSS 载荷，
// 外链必须带 rel="noopener noreferrer"。
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./MessageItem";

describe("Markdown XSS 消毒", () => {
  it("剥掉 <script> 标签", () => {
    const { container } = render(<Markdown text={'<script>alert("xss")</script>hi'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("hi");
  });

  it("剥掉 <img onerror> 事件属性", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)">'} />);
    const img = container.querySelector("img");
    if (img) {
      expect(img.hasAttribute("onerror")).toBe(false);
    }
    expect(container.innerHTML).not.toContain("onerror");
  });

  it("剥掉 javascript: 伪协议链接", () => {
    const { container } = render(<Markdown text={"[click](javascript:alert(1))"} />);
    const a = container.querySelector("a");
    expect(a).toBeTruthy();
    expect(a?.getAttribute("href") || "").not.toContain("javascript:");
  });

  it("外链自动加上 rel=noopener 与 target=_blank", () => {
    const { container } = render(<Markdown text={"[示例](https://example.com)"} />);
    const a = container.querySelector("a");
    expect(a).toBeTruthy();
    expect(a?.getAttribute("target")).toBe("_blank");
    expect(a?.getAttribute("rel")).toContain("noopener");
    expect(a?.getAttribute("rel")).toContain("noreferrer");
  });

  it("正常 markdown 链接/代码块仍可渲染", () => {
    const { container } = render(<Markdown text={"**粗体** `code` [x](https://x.com)"} />);
    expect(container.querySelector("strong")).toBeTruthy();
    expect(container.querySelector("code")).toBeTruthy();
  });
});
