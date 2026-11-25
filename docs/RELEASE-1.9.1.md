# PocketMind 1.9.1 补充版

- 版本：`1.9.1` / versionCode `191`
- APK：`PocketMind-1.9.1-arm64-debug.apk`，183 MB（191,605,847 字节），debug 签名
- SHA-256：`e2bf39c63bb7ce08a8ae49a7d5e137495c5c8c45e70efdf4f64fd2a390a47870`
- 下载：<https://aka.doubaocdn.com/s/V94oJF5ApA>
- rootfs 不变：Alpine 3.21.3 + Node 22 + Qwen Code 0.28.4 + MCP Filesystem 2025.8.21

1.9.0 把“能直接跑的东西”塞进去了，但用户还得自己知道它们在哪儿。1.9.1 补的是
**发现入口和操作路径**，不换 rootfs，不重复下载工具链。

## 这次补了什么

- **工作区「Linux 环境」新增内置 CLI 卡片**：读取 rootfs 内 package.json，直接显示
  `Qwen Code 0.28.4` 与 `MCP Filesystem 2025.8.21`；终端入口 `qwen`、MCP 命令
  `mcp-server-filesystem | /workspace` 写在卡片里。
- **MCP 一键填入**：设置 → MCP Client 增加「填入内置文件系统 MCP」按钮，自动启用
  `mcp_client` 并追加 `filesystem | mcp-server-filesystem | /workspace`，不覆盖已有服务器。
- **审批卡中文化**：`qwen_code` 现在显示为「运行 Qwen Code」，不再裸露内部工具名。
- **能力页提示**：任务与编排分组标明包含 Qwen Code。
- **状态接口扩展**：`GET /api/workspace/proot` 增加 `toolchain` 字段
  （`qwen`、`mcp_filesystem`、`node22`），只读 package.json，不启动进程。
- **预检更准确**：`qwen_code` 在 minirootfs 上会返回 `capability_unavailable`，
  而不是执行一条会 127 的 shell；缺少 API Key 时也不会先解压 585MB rootfs。

## 验证

| 门禁 | 结果 |
|---|---|
| 原生单测 | 109 / 109 |
| 原生 lint / assemble | 通过 |
| 前端 tsc + test + build | 83 / 83，构建通过 |
| 后端 tests | 58 / 58（上一轮） |
| 版本单源 | 1.9.1 / 191 三处一致 |
| 新增测试 | MCP 一键填入保存契约 + PRoot 状态含 toolchain |
| APK 静态检查 | 183MB；rootfs 资产 183,750,971 字节；5 个 arm64 原生库 |

## 仍未验证

与 1.9.0 相同的真机项：585MB 解压、Qwen 真实模型调用、MCP 工具调用、stdio 生命周期。
宿主没有 Android 设备，不能在真机上代替你点「允许」。
