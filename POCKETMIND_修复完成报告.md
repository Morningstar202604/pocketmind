# PocketMind 修复与安卓化完成报告

> **历史快照（2026-10-14 之前）**：本文记录的是当时代码的状态与结论，部分内容已被后续修复/新增取代；
> **现状请以 [CHANGELOG.md](CHANGELOG.md) 与代码为准**。保留本文仅供追溯当时的判断依据。

> 日期：2026-10-12
> 范围：P0/P1/P2 代码修复 + 测试全绿 + 文档一致性 + 增强版安卓 APK 实际构建
> 前置审查：见 `POCKETMIND_审查报告.md`

---

## 一、修复完成清单

| 编号 | 问题 | 修复内容 | 状态 |
|---|---|---|---|
| P0-1 | 依赖清单不完整，按文档部署无法启动 | `agentd/requirements.txt` 补 `slowapi`、`opentelemetry-sdk`、`opentelemetry-instrumentation-fastapi`、`opentelemetry-exporter-otlp-proto-grpc`，并注明 litellm 可选安装 | ✅ |
| P0-2 | `/api/memory/search` 缺鉴权，局域网可搜隐私记忆 | 该路由与 `/api/providers` 增加 `dependencies=[Depends(check_token)]`；`/api/health` 有意保留开放（供 Termux 开机自启/Docker HEALTHCHECK 探活） | ✅ |
| P1-1 | `GET /api/settings` 不返回 `agent_mode`，Plan/Act 状态无法恢复 | `config.py` 的 `DEFAULTS` 增加顶层 `"agent_mode": "act"`，`masked_settings` 随 DEFAULTS 输出 | ✅ |
| P1-2 | `mock_llm` 正则 `打电话|呼叫` 不匹配「打个电话」，演示/测试审批流失效 | 正则改为 `打电话|打个电话|呼叫` | ✅ |
| P1-3 | `--home` 只改 config 路径，数据库仍在默认目录（数据分裂） | 新增 `_rebuild_app_for_home()`：`main()` 解析 `--home` 后重建 `approval` 与 `app`，并关闭旧 store/scheduler 资源，使 `agent.db`/`jobs.db`/`checkpoints.db` 全部跟随新目录 | ✅ |
| P2-1 | `smoke/p3_robustness_unit.py` 引用已删除的 `agentd.main._masked_settings` | 改为从 `agentd.routes` 导入 `masked_settings`/`validate_job_payload` | ✅ |
| 测试补充 | 无鉴权漏洞回归用例 | `tests/security_test.py` 新增「安全-6：memory/search 路由带 check_token 依赖」 | ✅ |
| 文档 | `FINAL_REPORT.md` 声称 `eval_agent_utils.py` 存在、pytest 19/19 全绿、限流/OTEL 已上线 | 在报告顶部插入修订说明，纠正所有不实/过时内容 | ✅ |
| 文档 | README 徽章「141 backend tests」无法对应当前测试规模 | 改为「11 backend suites + 31 frontend」 | ✅ |
| 文档 | `make-apk.md` 描述过时（「30 行 Java」） | 重写为增强版 APK 说明（引导部署、构建、使用流程、技术选型理由） | ✅ |
| 安卓化 | 无引导部署能力的纯 WebView 壳 | 新增 `agentd_setup.html` 引导页 + `MainActivity` 的 Termux 一键部署/启动/检查 Bridge + `AndroidManifest` 增加 `<queries>` | ✅ |
| APK 构建 | — | `gradlew assembleDebug` 实际构建成功，产出 `app-debug.apk`（115 KB） | ✅ |

---

## 二、验证结果（全部实际运行）

### 后端

| 套件 | 结果 |
|---|---|
| `smoke/p0_regression_test.py` | ✅ 7/7 |
| `tests/security_test.py`（含新增鉴权用例） | ✅ 39/39 |
| `smoke/p3_robustness_unit.py` | ✅ 23/23 |
| `smoke/p3_perf_leak_test.py` | ✅ 5 过 / 1 跳过（POSIX 专用） |
| `smoke/test_mcp_server.py` | ✅ 10/10 |
| `smoke/test_telegram_gateway.py` | ✅ 18/18 |
| `smoke/m5_regression.py` | ✅ 6 过 / 0 失败 / 3 跳过（环境相关） |
| `smoke/p0_queue_test.py` | ✅ 3/3 |
| `smoke/p1_jobs_test.py` | ✅ 8/8 |
| `smoke/p2_plan_recipe_test.py` | ✅ 13/13 |
| `smoke/p3_robustness_api.py` | ✅ 6/6 |
| pytest `tests/eval_approval.py` | ✅ 7/7 |
| `ruff check agentd/ tests/ smoke/` | ✅ All checks passed |
| `python -m compileall agentd/` | ✅ 通过 |
| mock 服务启动 + `/api/health` | ✅ 组件全 ok |

### 前端

| 项 | 结果 |
|---|---|
| `npx tsc -b --noEmit` | ✅ 0 错误 |
| `npm run test`（vitest） | ✅ 31/31 |
| `npm run build` | ✅ 253KB JS（gzip 85.8KB）+ 22KB CSS |

### 关键修复的专项验证

- **鉴权**：设置 `server.token` 后，无令牌访问 `/api/memory/search`、`/api/providers`、`/api/memory`、`/api/sessions` 全部 **401**；`/api/health` 保持 200（探活）。
- **`agent_mode`**：`GET /api/settings` 返回 `agent_mode`（默认 `act`，切 plan 后返回 `plan`）。
- **`--home`**：以 `--home $TEMP/pa-home-test` 启动后，`agent.db`/`jobs.db`/`checkpoints.db` 全部写入新目录，健康检查正常。

---

## 三、安卓 APK 产物

| 项 | 值 |
|---|---|
| 工程 | `termux/apk-shell/` |
| 产物 | `termux/apk-shell/app/build/outputs/apk/debug/app-debug.apk`（115 KB，debug 签名） |
| 功能 | 首次启动引导页（检测 agentd 未就绪）→ 一键部署（Termux RunCommandService 执行 `install.sh` + `start.sh`）→ 自动检测就绪进入 WebView；前台服务保活；错误页重试；JS Bridge（复制/通知/振动/唤醒 Termux/F-Droid） |
| 形态 | 仍依赖 Termux 承载 Python 运行时（当前 Chaquopy 无法编译 pydantic-core，见 `termux/make-apk.md` 技术说明） |

构建命令（已在本机验证通过）：

```bash
cd termux/apk-shell
./gradlew assembleDebug        # 产物在 app/build/outputs/apk/debug/app-debug.apk
```

发布签名：`./gradlew bundleRelease` 或 Android Studio → Generate Signed APK（需要自备签名）。

---

## 四、使用说明（真机）

1. 手机安装 **Termux（F-Droid 版）**；
2. 安装 `app-debug.apk`，打开进入引导页；
3. 点击「一键部署」，首次在 Termux 中允许「允许运行命令」授权，部署自动完成（3-8 分钟）；
4. 回到 App 点击「检查连接」，就绪后进入聊天界面；
5. 在设置中选择 LLM 厂商、填入 API Key，开始使用。

> 若已手动部署过 Termux 后端，直接点「已部署过，仅启动服务」即可。

---

## 五、剩余限制与后续建议

| 项 | 说明 | 建议 |
|---|---|---|
| 真机验证 | 本机为 Windows，无法验证 termux-api 实参行为、Termux RunCommandService 授权弹窗、通知权限等 | 按 `termux/device-matrix.md` 真机清单逐项勾选 |
| APK 仍需 Termux | Chaquopy 无法编译 pydantic-core（Rust），暂无法完全内嵌后端 | 关注 pydantic-core 社区 Android wheel 进展；后续可迁移到 Chaquopy 方案 |
| `--lan` 下 `/api/health` 开放 | 为探活保留，泄露组件状态（低危） | 如部署公网，建议在前置反向代理层限制 `/api/health` |
| dist 未提交 | 工作区前端源码与仓库 HEAD 的 dist 不一致（工作区本身未提交状态） | 正式提交时重新 `npm run build` 并一并提交 dist，通过 CI 的 dist freshness 门禁 |
| 工作区未提交 | 所有修复均为工作区修改，尚未 git commit | 按需提交；提交前建议本地跑一遍 `bash termux/install.sh` + `bash termux/start.sh` 链路 |

---

## 六、结论

当前工作区已经从「报告全绿但实际跑不起来」修复为：

- **可部署**：按 `requirements.txt` 安装后服务可正常启动（mock 实测通过）；
- **安全**：记忆搜索等敏感端点已纳入令牌保护；
- **测试全绿**：11 个后端 smoke 套件 + 7 个 pytest + 31 个前端测试全部通过；
- **文档一致**：FINAL_REPORT/README/make-apk 已与实际状态对齐；
- **安卓化**：增强版 APK 已实际构建成功，具备「首次引导一键部署 + 客户端 + 保活」的完整 App 体验。

---

## 七、独立审阅（审阅者子代理）发现并已修复的问题

> 在全部测试通过后，由独立审阅者对 8 个检查点进行代码级复核，确认后端/前端修复均无问题，并发现 4 个 APK 侧问题，已全部修复：

| 级别 | 问题 | 修复 |
|---|---|---|
| P1 | AndroidManifest 未声明 com.termux.permission.RUN_COMMAND，一键部署/启动服务必然失败 | Manifest 增加该权限；
unInTermux 捕获 SecurityException 时提示用户授权（而非静默打开 Termux）；引导页补充两步授权说明 |
| P2 | 振动权限名 VIBRATE 缺少 ndroid.permission. 前缀（非法权限名，振动静默失效） | 改为 ndroid.permission.VIBRATE |
| P3 | Android 13+ 通知权限授权后不补发缓存通知 | 通知发送逻辑移到 MainActivity，onRequestPermissionsResult 授权成功后补发 |
| P3 | 
umpy 为 voice.py 直接 import 但未在 requirements 声明 | requirements 可选注释区补充 # numpy（未安装时自动降级） |

修复后 APK 已重新构建成功（pp-debug.apk，125 KB）。后端 ruff/安全测试再次确认通过。


---

## 八、用户体验优化与细节打磨

> 从用户使用逻辑和流程出发，对前端交互做了以下优化（全部通过 tsc / vitest 31/31 / vite build 验证）：

| 优化点 | 原问题 | 改动 |
|---|---|---|
| 首次使用引导 | 未配置 LLM 时空状态页只提示文字，用户不知道去哪配置；示例任务不可用却仍展示 | EmptyState 未配置时显示「前往设置」按钮并隐藏示例任务，点击直达设置面板 |
| 生成中输入 | 生成中 	extarea disabled，排队机制存在但 UI 不允许输入，用户只能干等 | 生成中允许继续输入；输入内容时显示发送按钮（自动排队），无输入时显示停止按钮；占位/提示文案同步更新 |
| 删除确认 | 删除会话/定时任务无二次确认，误删不可恢复 | 删除前弹确认框，提示级联影响 |
| 审批倒计时 | 审批卡无超时提示，用户不知道多久自动拒绝 | 审批等待时显示「N 秒后自动拒绝」实时倒计时（与服务端默认 120s 对齐），超时态提示自动拒绝 |
| 错误提示 | 顶部错误只能靠重发消息清空 | 错误条增加 ✕ 关闭按钮（store 新增 clearError），样式改为醒目底色条 |
| 样式细节 | 空状态按钮、审批倒计时、错误条缺乏针对性样式 | 补充 .empty-setup / .approval-countdown / .composer-err 系列样式 |

### 验证结果
- 
px tsc -b --noEmit：0 错误
- 
pm run test（vitest）：31/31 通过
- 
pm run build：通过（256.56KB JS + 22.52KB CSS）
- mock 服务访问 /：正确托管新构建产物（index-CI4nB4W5.js）


---

## 九、第二轮体验优化与细节打磨

> 在上一轮基础上，继续从用户使用逻辑出发，覆盖消息浏览、计划决策、定时任务、设置、数据管理等场景。全部通过 tsc / vitest 31/31 / vite build / 后端 11 套件验证。

| 优化点 | 原问题 | 改动 |
|---|---|---|
| 消息时间戳 | 消息列表没有时间信息，无法区分新旧 | 后端 get_messages 返回 created_at；前端消息模型与历史恢复保留时间，用户/助手消息旁显示 HH:MM |
| Esc 停止生成 | 停止按钮 title 写「Esc」但实际无快捷键 | Composer 增加全局 keydown 监听，Esc 与停止按钮等效 |
| 计划可忽略 | Plan 模式下计划卡片只能「批准/修改」，无法放弃 | PlanCard 增加「忽略计划」按钮（store 新增 ignorePlan），忽略后卡片显示已忽略，可继续发新指令 |
| 任务时间完整显示 | 定时任务下次运行时间只显示 MM-DD HH:MM，无年份、依赖 ISO 格式 | 新增 
ormatNextRun，显示本地化完整时间（含年份） |
| 记忆清理生效 | memory.retention_days 配置存在但从未执行 | lifespan 启动时调用 store.cleanup_expired(retention_days)，过期记忆/消息自动清理 |
| LLM 连接测试 | 设置里填完 Key 只能保存后试聊才知道能不能用 | 新增 POST /api/llm/test 端点 + 设置面板「测试连接」按钮，即时返回成功/失败与模型名 |
| 备份文案修正 | 设置页声称「凭文件恢复」但实际无导入功能（误导） | 文案改为「用于本地备份留存」 |
| 会话搜索 | 会话多了只能手动翻找 | 会话抽屉新增搜索框，按标题实时过滤 |

### 验证结果
- 前端：	sc 0 错误、vitest 31/31、
ite build 通过
- 后端：ruff/compileall 通过、11 个 smoke 套件全部退出码 0
- 实测：/api/llm/test mock 模式返回 {ok:true}，配置后返回模型名；get_messages 返回 created_at