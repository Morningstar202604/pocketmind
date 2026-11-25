# 定位：手机里的本地 Agent 工作站

PocketMind 不是「聊天 App + 工具」，也不是云 IDE 的遥控器。它的产品定义是：

> **Android 上的本地 Agent 运行时 + 轻量代码工作站。**

上半身是手机系统 Agent：39 个原生工具 + 内置 `qwen_code` CLI、服务端强制审批、定时任务、审批审计。
下半身是代码工作站：文件树、JGit（状态/分支/合并/三路冲突/撤销）、PTY、
PRoot Alpine 3.21（Node 22/Python/Git/GCC、Qwen Code、MCP 文件系统、自检）、Git 远程克隆/拉取、HTTP/stdio MCP、
只读子代理。数据默认留在设备，模型请求只发往用户配置的端点。

## 它和谁竞争

2026 年的「手机 Agent」已经分成三条赛道，PocketMind 站在 1 和 3 的交叉口：

1. **本地 coding agent / IDE**：Nexon、AnyClaw、Termux 上的各种 CLI agent。
2. **云/远程会话客户端**：Claude Code 手机端、Cursor iOS、GitHub Codespaces。
3. **GUI 手机 Agent**：AutoGLM、OpenClaw 的手机节点。

| 产品 | 强在哪 | 软肋 | 对 PocketMind 的意义 |
|---|---|---|---|
| **AnyClaw** | 单 APK 内嵌 OpenClaw/Codex/Claude Code 等五个 agent，Termux 派生 Linux（sh/apt/Node 24/Python），免 root/Termux | “Full Auto-Approval”没有审批闸门；体积/攻击面大；其 Claude Code 分支页面自述基于泄露代码改写，商用分发有合规灰区 | 编程广度最强；PocketMind 只能用“服务端审批 + 手机系统工具 + 轻量”错位竞争 |
| **Nexon** | Termux + Flutter + Python bridge，37 家模型 BYOK、真实 shell、行级 patch、快照回滚、权限审查、deep research | 依赖 Termux；偏编程/研究，不是手机系统 Agent；版本很早期 | 最直接的 coding 对手；PocketMind 必须证明“无 Termux 单 APK”的体验优势 |
| **CodeTalk** | 端侧本地模型、离线、直接生成 Kotlin/Compose APK，面向非专业用户 | 只做 Android App 生成，不是通用仓库/Agent 工作站 | 抢“离线、零 API 费用”的心智；PocketMind 不做端侧模型就很难在这个点赢 |
| **Claude Code 手机端 / Cursor iOS** | 模型/agent 质量、生态最强；手机可开云会话或远程控制电脑 | 手机是客户端，代码默认不在手机上；需要账号/云/自己的主机 | 它们教育了市场，也把“手机 Agent”的标准拉高；PocketMind 打本地/隐私/无订阅 |
| **OpenClaw Android** | 官方原生 App，Gateway 多端、语音、手机权限工具、审批、Wear OS | 工作区只读，不是本地 coding runtime | 证明“手机 Agent”需求真实；PocketMind 的本地写代码是差异 |
| **AutoGLM** | GUI 操作真机/云手机，跨 App 点外卖、订票、发消息 | 不写代码、不管仓库 | 另一条赛道；会抢“手机 Agent”这个词，但不会替代开发工作站 |

## 能力账本

**强项**

- **安全边界**：审批在服务端请求路径执行，模型绕不过；风险分级、五档决策、
  120 秒超时拒绝、只读审计、写入/删除可撤销。AnyClaw 的全自动批准正好让出了这个位置。
- **手机系统能力**：短信、通知、定位、相机、剪贴板、手电筒、shell 等 39 个原生工具，
  纯 coding agent 不具备。
- **本地工作区 + JGit**：分支、提交、预合并、三路冲突块、合并撤销、远程克隆/拉取，
  手机上少见。
- **单 APK 自带运行时**：1.9.2 为 183MB，内含 176MB Alpine 3.21 开发工具链（Node 22/Python/Git/GCC）与 PRoot，并内置 Qwen Code 0.28.4 与 MCP 文件系统；无 Termux/外部 Python/Node 依赖。

**弱项**

- **编程纵深**：1.9.2 已内置 Node 22/Python 3.12/Git/GCC 与 Qwen Code 的 176MB 离线工具链，
  但真机执行仍待验证；它仍是 musl Alpine，不是 AnyClaw 那种完整 glibc Linux。
- **Agent 生态**：已内置单 agent CLI（Qwen Code）与文件系统 MCP server，但还没有多 agent 编排；MCP 服务端托管仍缺。
- **和 850MB 同行对比**：我们补的是运行时 + 一个内置 agent CLI，不是三套 agent 运行时或本地模型；
  逐项拆解见 [`COMPETITOR-AUDIT.md`](COMPETITOR-AUDIT.md)。
- **成熟度**：debug APK、未上架、真机全链路未验；PRoot/stdio 仍列在
  [DEVICE-TEST.md](DEVICE-TEST.md)。
- **模型/研究**：内置豆包/DeepSeek/Qwen/GLM/Kimi 等国内预设，但没有 Nexon 的 37 家 +
  deep research + artifacts 深度。

## 叙事建议

不要把自己包装成“手机 AI IDE”去和 AnyClaw/Nexon 拼功能数量。更锋利的说法：

> **“手机里的本地 Agent 工作站：代码、命令、Git 都在本机，破坏性操作必须过审批。”**

一句话打三个差异：**本地、审批、手机系统工具**。目标用户不是“想写代码的普通人”，
而是“已经把手机当第二台机器的开发者/学生/折腾党”。

## 下一步优先级

1. **P0** 真机跑通 PRoot + 安装启动 + 审批链路；没有这个，“AI 开发环境”是纸面能力。
2. **P0（1.9 已补，待真机）** 把 PRoot 从“能开 Alpine shell”升级成“能跑 Node/Python、Qwen Code、MCP 文件系统、跑测试”的一键环境。
3. **P1** Git 远程导入 + 依赖引导 + 任务模板；这是“工作站”和“玩具”的分水岭。
4. **P1** 把审批/审计做成品牌功能（风险可视化、diff 预览、可回滚），填 AnyClaw 让出的空位。
5. **P2** MCP stdio 服务端、子代理编排、会话恢复；先不要做云同步/多端。

## 合规

- PRoot 是 GPL 系，分发 APK 必须附源码/许可说明；见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- AnyClaw 的“泄露 Claude Code 代码”路线不要参考；本项目的子代理与 MCP 都是独立实现。

## 参考

- Nexon: <https://nexonai.me/>
- AnyClaw: <https://openclawandroid.github.io/openclaw-android-assistant/>
- CodeTalk: <http://www.shturl.cc/94794c2f733a884b1df0e857b518ed1c>
- Claude Code mobile: <https://code.claude.com/docs/en/mobile>
- OpenClaw Android: <https://docs.openclaw.ai/platforms/android>
- AutoGLM: <https://apps.apple.com/cn/app/autoglm/id6748391183>
