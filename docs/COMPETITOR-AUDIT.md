# 同行拆解：APK 大小与能力账本

调查时间：2026-11-25。数据来自公开商店页与项目文档，安装后占用会更大。

## 先看数字

| 产品 | 分发体积 | 体积里装了什么 | 是否要 Termux/服务器 |
|---|---|---|---|
| **PocketMind 1.8.0** | **131MB APK**；首次解压 123MB rootfs，占 ~372MB | Kotlin 引擎、JGit、PRoot、Alpine、Node 20.15、npm 10.9、Python 3.12、pip 24、Git 2.45、GCC 13.2、make 4.4、39 个原生工具 | 不要 |
| **PocketMind 1.9.1（当前）** | **183MB APK**；首次解压 176MB rootfs，占 ~585MB | 在 1.8 基础上换 Alpine 3.21/Node 22.23/Python 3.12.16/Git 2.47/GCC 14.2；内置 **Qwen Code 0.28.4** 与 **MCP 文件系统 server**；39 个原生工具 + `qwen_code` | 不要 |
| **AnyClaw** | **850.53MB**（Google Play，2026-11-04） | OpenClaw + Codex CLI + Claw Code/OpenClaude 三套 agent、内嵌完整 Linux、Node/SSL/ARM64 运行时 | 不要 |
| **OpenClaw Android（standalone）** | **27.7MB APK + ~300MB 首次下载** | Node 22、Python 3.13、Go 1.26、OpenClaw 688 个包；是 Gateway/助手，不是 coding IDE | 不要，但首次要联网 |
| **OpenClaw 官方 Android** | 商店 APK（未公开体积） | 只是 Gateway 的配套节点；Gateway 跑在电脑/WSL2 | 要 Gateway |
| **Nexon** | 商店未公开 APK 体积 | Flutter UI + Termux/Python bridge；真实占用 = Termux + Python + provider SDK | 要 Termux |
| **CodeTalk** | APK + 模型 | 端侧 2B/3B/7B 模型；模型本身是 GB 级，APK 不是全部 | 不要 |
| **Claude Code 手机端 / Cursor iOS** | 几十 MB 级 | 云会话/远程客户端，代码不在手机 | 要服务器 |
| **AutoGLM** | 几十 MB 级 | GUI 手机 Agent，另一条赛道 | 要云手机/服务 |

结论：**850MB 的 AnyClaw 不是 UI 比我们多 840MB，而是把三套 agent 运行时和一个完整 Linux 都塞进去了。**
OpenClaw standalone 的 27.7MB 也只装了壳，真正能力在 300MB 首次下载里。CodeTalk 的体积大头是本地模型。

## 我们原来少的是什么

1. **运行时是空的**：1.7 的 9MB APK 只有 Alpine minirootfs，Node/Python/Git 要首次 `apk add`，断网就是空壳。**1.8 已补预装工具链；1.9 再把 Qwen Code 与 MCP server 塞进去，解压即用。**
2. **没有第二、第三个 agent**：AnyClaw 有 OpenClaw/Codex/Claw Code 三套；1.9 补了 Qwen Code CLI 作为第二 agent，但还没有三套并行编排。
3. **没有本地模型**：CodeTalk 的 2B-7B 模型在端侧跑；我们只有云端 provider。
4. **没有完整 glibc 发行版**：Alpine/musl 对部分 Python wheel、Node 原生模块、预编译二进制不友好；AnyClaw 用完整 Linux。
5. **IDE 纵深不够**：我们有 CodeMirror、文件树、diff、Git、终端；没有 LSP/补全/跳转/调试器/项目索引，也没有“在手机上编译 APK”的 Gradle/SDK 管线。
6. **没有 MCP 服务端托管**：我们只是 MCP 客户端；AnyClaw/Codex 能起服务端、接 6 种 transport。
7. **没有 deep research / artifacts / 多代理并行**：Nexon 这批产品的差异项。
8. **没有本地离线 TTS/语音模型**：仍是系统 TTS。

## 1.9.1 补完后，哪些差距还在

| 能力 | 状态 |
|---|---|
| 离线 Node/Python/Git/C 工具链 | **已补** |
| 内置 agent CLI（Qwen Code） | **已补**；单 CLI，非多 agent 编排 |
| 内置 MCP filesystem server | **已补**；托管/反向暴露仍未做 |
| 多 agent 运行时（Codex/Claude Code/OpenClaw） | 未做；需要决定是否引入第三方 CLI 与合规审查 |
| 本地 2B-7B 模型 | 未做；模型 + llama.cpp 至少 1.5-2GB |
| 完整 glibc 发行版 | 未做；Alpine 仍是当前选择 |
| LSP/补全/跳转/调试 | 未做 |
| 手机上编译 APK | 未做；需要 Android SDK/Gradle，数百 MB |
| MCP 服务端托管 | 未做；当前是客户端 |
| deep research/artifacts | 未做 |
| 多代理并行/可写子代理 | 只读子代理已有；并行/可写未做 |

## 下一步优先级（按“用户能感知”排序）

1. **P0：可写/并行子代理 + 任务编排**。不增加多少 MB，但直接对标 AnyClaw 的 multi-agent swarms。
2. **P1：LSP/项目索引**。让 CodeMirror 从“能编辑”变成“能补全、能跳转、能报错”。
3. **P2：MCP 服务端托管**。把现有 MCP 客户端能力反过来，让别的 agent 接 PocketMind 的工具。
4. **P3：本地小模型**。2B 量化 + llama.cpp，至少 +1.5GB；是否值得取决于目标机型。
5. **P4：完整 glibc 发行版**。Ubuntu/Debian rootfs + apt，解决 wheel/原生模块兼容，代价 +100MB 起。

不建议为了“看起来像同行”把 850MB 全塞进来；PocketMind 的差异仍是**单 APK、本地、审批、手机系统工具**。
但“9MB 空壳”这个短板，1.9.1 已经补到“有运行时、也有能直接跑的 agent CLI”。

## 来源

- AnyClaw Google Play 体积 850.53MB：<https://chrome-stats.com/d/gptos.intelligence.assistant>
- AnyClaw 三 agent/内嵌 Linux：<https://github.com/Heretek-AI/openclaw-android-assistant>
- OpenClaw standalone 27.7MB + ~300MB：<https://github.com/Lafourkad/openclaw-android>
- OpenClaw 官方 Android 是 Gateway 配套节点：<https://docs.openclaw.ai/platforms/android>
- CodeTalk 端侧 2B-7B 模型：<https://play.google.com/store/apps/details?id=com.lutraapps.codetalk>
