# 真机测试清单（v1.7.0）

本地门禁只能证明 JVM/Robolectric 与构建；PRoot、PTY、stdio MCP、真机权限
必须在物理 Android 设备上跑。以下按顺序执行，任一步失败都先停下来记录现象。

## 0. 安装与启动

- [ ] `adb install -r app-debug.apk`（arm64 设备；x86_64 模拟器只有 `libpty.so`，没有 PRoot）
- [ ] 打开 App，完成提供商 → API Key → 权限引导
- [ ] 发一条普通消息，确认 SSE 流式回复
- [ ] `GET /api/health` 返回 `ok`（App 内诊断页或 `adb forward` + curl）

## 1. 原生工作区

- [ ] 新建目录/文件，编辑保存前出现 diff，确认后落盘
- [ ] 变更面板出现记录，单条撤销与「撤销全部」都能恢复
- [ ] 后台任务 `sleep 5` 启动、看到增量输出、可取消
- [ ] Android Shell 终端可输入 `echo ok`，断线 30 秒内重连能接管

## 2. PRoot Linux（核心）

进入工作区 → 顶部「Linux 环境」：

- [ ] 准备至少 800MB 空闲空间；首次进入会解压 123MB 包成约 372MB，通常 30–120 秒
- [ ] 状态显示 `Alpine rootfs 已就绪`，`bootstrap=done`，profile 为 `prebuilt-full`
  （若显示未安装，点「安装开发环境」也应直接命中预装标记、不联网）
- [ ] 点「环境自检」，逐项应显示：
  - `alpine` = `3.20.x`
  - `uid` = `0`（PRoot 的 `-0` 模拟，不是真 root）
  - `apk` 可用
  - `node` / `npm` / `python3` / `pip3` / `git` 可用
  - 若装了 full，`gcc` / `make` 可用
- [ ] 终端切到「Linux · PRoot」，执行：
  ```sh
  cat /etc/alpine-release
  node --version
  python3 --version
  git --version
  cd /workspace && echo hi > probe.txt
  ```
- [ ] 回到工作区文件树，能看到并打开 `probe.txt`
- [ ] 在子目录里开 Linux 终端，`pwd` 应为 `/workspace`；不要在应用根目录开（guest 会看到根目录配置）

**失败时记录**：

- 「Linux 环境」面板的自检输出 / 安装日志全文
- `adb logcat -s PocketMind:* AndroidRuntime:* linker:*` 中 `proot`、`libproot`、`PROOT_LOADER`、`ptrace` 相关行
- 设备 ABI（`adb shell getprop ro.product.cpu.abi`）、Android 版本
- APK 内是否真有 `lib/arm64-v8a/libproot.so` 与 `assets/proot/alpine-dev-rootfs.tar.gzip`
  （`unzip -l app-debug.apk | grep -E 'proot|alpine'`）
- 安装后 `nativeLibraryDir` 是否有 `libproot.so`、`libproot-loader.so`、`libtalloc.so`、`libandroid-shmem.so`
  （`adb shell run-as com.pocket.agent ls -l /data/app/*/lib/arm64` 或 `pm path` 后拼路径）

## 3. Git 远程

- [ ] Git 面板 → 克隆 `https://github.com/octocat/Hello-World.git` 到 `repos/hello`
- [ ] 克隆后状态 `main`、clean、文件树可见
- [ ] 新建分支、改文件、提交；预合并另一分支，出现冲突时三路块可选 ours/theirs/manual
- [ ] 对已有 remote 点「拉取 origin」，无冲突时状态更新；有冲突时进入冲突面板
- [ ] 私有库用 `https://<token>@github.com/...` 测试（不要把 token 提交进仓库）

## 4. MCP

### HTTP

- [ ] 设置 → MCP Client 启用，填 `demo | https://example.com/mcp`
- [ ] 重启服务后 `GET /api/mcp` 显示 `transport=http`、工具数 > 0
- [ ] 在对话里调用 `mcp__demo__...`，审批卡出现且能批准/拒绝

### stdio over PRoot

- [ ] 预装工具链已解压（可直接用 node/npx；无需再安装 minimal）
- [ ] 设置里填 `filesystem | npx | -y @modelcontextprotocol/server-filesystem /workspace`
- [ ] 重启服务；`GET /api/mcp` 该服务器 `transport=stdio` 且 `ok=true`
- [ ] 调用一个只读工具；确认 `command` 与 `args` 拼成一行在 guest 内执行
- [ ] 关闭服务后进程应被回收（`ps -A | grep node` 不应残留）

## 5. 子代理与审计

- [ ] 对话里让 Agent「用 subagent 看一下工作区有哪些文件」
- [ ] 子代理只读：让它写文件应被拒绝，原文件不变
- [ ] 子代理结束后会话列表不应留下“子代理”临时会话
- [ ] 工作区「审计」面板能看到刚才的 approve/deny 记录；记录里没有命令参数/文件内容

## 6. 回归

- [ ] 重启手机后前台服务、定时任务、通知仍在
- [ ] 导出/导入备份可 round-trip
- [ ] 切换深色/浅色主题，工作区面板与终端不破版
- [ ] Android 10+ 权限拒绝时，工具卡能一键拉起系统授权

## 结果回传

把失败步骤、截图、日志尾部 200 行发回；不要只说“打不开”。
PRoot 问题优先看自检输出与 `PROOT_LOADER` 路径，二者能区分 loader 未解压、
exec 权限、ptrace 被 SELinux 拒绝、rootfs symlink 解析四类原因。
