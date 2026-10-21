# 在 Windows 上用 Android 模拟器验证 Termux

真机不可得时，用官方 Android 模拟器验证「安装脚本能不能装、Python 能不能起来」。
本文记录**实测**结论（Ubuntu 24.04 容器与 Windows 上均无法覆盖的部分）。

## 结论先行

| 能验证 | 不能验证 |
|---|---|
| `install.sh` 的包检测与幂等性 | 真实硬件能力（短信/电话/定位/传感器/电池） |
| Python 与依赖能否装上、能否 import | aarch64（真机多为 arm64；本方案是 x86_64 镜像） |
| `agentd` 能否在 Android 内核上启动、监听端口 | 电池优化 / 唤醒锁 / 锁屏后被杀的电源行为 |
| Termux 的 SELinux 与路径权限约束 | termux-api 的真实返回值格式 |
| `boot.sh` / `start.sh` 的路径与解释器假设 | 编译耗时在真实 SoC 上的表现 |

**一句话**：模拟器能证明「装得上、起得来」，不能证明「手机能力可用」。

## 快速开始

```bash
# 1. 一次性准备（约 5GB 下载、10 分钟；需 Hyper-V 平台已启用）
bash tools/android-vm/provision.sh

# 2. 在 Termux 里执行命令（自动处理 run-as / PATH / 引号转义）
bash tools/android-vm/termux-run.sh 'python --version'

# 3. 推送文件进去
bash tools/android-vm/termux-run.sh --push agentd/requirements.txt

# 4. 只检查环境
bash tools/android-vm/provision.sh --check
```

## 实测踩到的坑（按影响排序）

### 1. APK 必须校验完整性，GitHub Release 直连会截断
35,189,933 字节的 APK，直连先后得到 3.8MB / 7MB / 19.9MB 三种截断结果，
`adb install` 报 `INSTALL_PARSE_FAILED_NOT_APK`。必须用 `curl -C -` 断点续传，
直到字节数等于 GitHub API 返回的 `size`。`provision.sh` 已内置该循环与校验。

### 2. SELinux 禁止普通 shell 读应用私有目录
`adb shell ls /data/data/com.termux` → `Permission denied`。必须走
`run-as com.termux`，而它只对 **debuggable** 构建有效——所以只能用 Termux 官方
debug APK。`termux-run.sh` 封装了这一点。

### 3. 三层引号转义必炸
`adb shell "run-as ... bash -c '...'"` 嵌套到第三层时，宿主 sh 会二次解析。
解法：把命令 base64 编码后传输，在远端 decode 执行。实测唯一稳定方案。

### 4. `run-as` 下 `$PREFIX` / `$HOME` 未设置
Termux 的 `$PREFIX`（`/data/data/com.termux/files/usr`）不是系统环境变量，
在 `run-as` 启动的 shell 里为空。所有脚本必须显式 export 或用绝对路径。

### 5. Termux 默认源与 PyPI 官方源都不通
- Termux apt 默认镜像（浙大）实测约 1KB/s；换 `mirrors.ustc.edu.cn/termux` 后正常。
- `pypi.org` 在模拟器网络下 `Connection refused`；清华/阿里云镜像正常。
`install.sh` 已加入镜像回退。

### 6. `pydantic-core` 需要系统 Rust，rustup 装不上
pip 构建 `pydantic-core` 时会尝试用 rustup 安装 Rust，但
`x86_64-unknown-linux-android` **不是 rustup 支持的 target**，直接失败：

```
Target triple not supported by rustup: x86_64-unknown-linux-android
Rust not found, installing into a temporary directory
```

必须先 `pkg install rust`（含 `rust-std-x86-64-linux-android`），让 pip 用系统 rustc。
这验证了 `install.sh` 里 `pkg install rust` 是**必需步骤**而非可选优化。

### 7. 编译耗时远超文档声称 —— 且真凶是 grpcio
`install.sh` 原写「约 3-8 分钟」。实测在 x86_64 模拟器上超过 1 小时仍未装完。

排查发现真正的瓶颈**不是** `pydantic-core`（Rust，几分钟），而是
`opentelemetry-exporter-otlp-proto-grpc` → **`grpcio`**：它在 Termux 上没有
预编译 wheel，要现场编译 BoringSSL / Abseil / gRPC 全套 C++（实测并发 5 个
clang++ 进程，单文件 `ssl_lib.cc` 就要很久，内存峰值 2GB+）。

而这个导出器是**可选功能**：`telemetry.py` 只在
`OTEL_TRACES_EXPORTER=otlp` 时才 `import` 它，默认路径完全用不到。
把它列进主依赖，等于让每个 Termux 用户为一个默认关闭的功能编译整套 gRPC。

已从 `requirements.txt` 移出主清单并注明按需安装。

### 7a. Tempux 有预编译的 cryptography / rpds-py，必须用
实测依赖链：`mcp` → `pyjwt[crypto]` → **`cryptography`** → `cffi`。
`cryptography` 的 Rust + OpenSSL 绑定在模拟器上编译 30 分钟以上，
且中途把 5.8G 的 /data 吃到只剩 0（报 `No space left on device`，
随后 rustc 因写入失败 SIGSEGV，看起来像编译器 bug，实际是磁盘满）。

Termux 仓库里有预编译 deb，`apt-cache policy` 可查：
- `python-cryptography` 50.0.2
- `python-rpds-py` 2026.9.1
- `python-numpy` / `python-lxml`

先 `pkg install` 这几个，pip 会复用系统版本不再编译。
注意：Termux 的 `python-cryptography` 包内部还会 `pip install cffi`，
且它默认打 pypi.org（在模拟器里不通）——需要 `PIP_INDEX_URL` 指到镜像。

### 7b. pydantic-core 没有 Android wheel
用 `pip download --only-binary=:all:` 验证：`pydantic-core` 在 PyPI 上只有
`0.0.1` 这个占位包提供纯 Python wheel，真实版本全部需要源码编译。
所以 `pkg install rust` 是硬性前提，没有绕过的办法。

### 8. apt 锁竞争
前一个 apt 未结束时启动第二个会无限等待 `lock-frontend`。且 `adb shell kill`
无权限杀 Termux 用户进程，必须 `run-as` 下 kill，再清 `lock-frontend` / `lock`。

## 与容器验证的分工

| 检查类型 | 容器（Ubuntu） | Android 模拟器 | 真机 |
|---|---|---|---|
| 业务逻辑 / 单元测试 / 回归 | ✅ 主战场 | 过重，不必要 | 过重 |
| POSIX 权限语义（chmod 600/700） | ✅ | ✅ | ✅ |
| 安装脚本 / 依赖可装性 | 部分（非 Termux 包管理） | **✅ 关键** | ✅ |
| termux-api 硬件能力 | ❌ | ❌ | **✅ 唯一** |
| aarch64 编译 | ❌ | ❌ | **✅ 唯一** |

结论：**容器跑逻辑，模拟器跑安装，真机跑硬件**。三者不互相替代。

## 实测结论（2026-10-21 完整跑通）

### 成功验证的完整链路

```
Windows 11 + HypervisorPresent=True
  └─ Android SDK (cmdline-tools + emulator + android-35 x86_64)   4.8 GB
      └─ AVD termux_test，无头启动约 40s
          └─ Termux 0.118.3 debug APK（校验 35,189,933 字节）
              └─ bootstrap 390 个命令
                  ├─ pkg install python        → Python 3.14.8 + pip 26.2.2
                  ├─ pkg install rust          → rust 1.99.1（pydantic-core 编译前提）
                  ├─ pkg install python-cryptography python-rpds-py（预编译，省 30 分钟）
                  ├─ pip install -r requirements.txt（阿里云镜像）→ 46 个包，耗时 2196s
                  ├─ python -m agentd.main --mock --port 8787  → 服务起来了
                  ├─ /api/health → {"ok":true,"components":{"db":"ok","llm":"ready","scheduler":"ok"}}
                  └─ POST /api/chat → 收到流式回复
```

### 只在 Android 上暴露的缺陷

**`zoneinfo.ZoneInfoNotFoundError: 'No time zone found with key GMT'`**
—— Android/Termux 没有系统 tzdata，APScheduler 构造时猜本地时区直接抛异常，
服务连端口都没绑上就退出。Windows 与 Docker 都有 tzdata，**永远复现不了**。
已修：`SchedulerService` 显式解析时区，按 TZ → Asia/Shanghai → UTC → 固定偏移
四级降级，永不因时区失败。

### 安装耗时实测

| 阶段 | 耗时 |
|---|---|
| SDK 下载 + 解压 | ~5 分钟（1.5GB+ 下载） |
| 模拟器冷启动 | ~40 秒 |
| Termux bootstrap | ~1 分钟 |
| `pkg install python rust` | ~4 分钟 |
| `pip install -r requirements.txt`（含 pydantic-core 编译） | **2196 秒 ≈ 37 分钟** |
| **合计（首次）** | **约 50 分钟** |

其中 pip 阶段在 x86_64 模拟器上的耗时远高于真机（模拟器有虚拟化开销），
但 `pydantic-core` 的 Rust 编译在真机上同样需要数分钟到十几分钟。

### 仍然无法验证的

- **termux-api 硬件能力**：模拟器没有真实短信/电话/定位/传感器，
  即使装上 `termux-api` 也只会返回空或错误。
- **aarch64 编译**：本方案是 x86_64 镜像；真机多为 arm64，
  `pydantic-core` 的编译路径与耗时不同。
- **电源管理**：电池优化、锁屏后进程被杀、唤醒锁的真实行为。
- **真实 SoC 性能**：模拟器 CPU 与手机 SoC 差异大，耗时不可直接外推。
