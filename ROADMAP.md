# Roadmap

Status legend: **shipped** · **in progress** · **planned** · **exploring**

This roadmap states what is real today and what is deliberately not built yet.
Items move into [CHANGELOG.md](CHANGELOG.md) when they ship.

## Now — 1.7.x (current)

| Item | Status | Notes |
|---|---|---|
| **Kotlin native engine (single-APK core)** | shipped | In-process HTTP/SSE + SQLite + agent loop + approvals + 39 native tools; Termux no longer required. |
| Server-enforced approval for Web and Telegram agent paths | shipped | Three-level grants, global deny list, 120 s auto-deny (native + Python). |
| 51 tools in the Python runtime / 39 in the native runtime | shipped | `safe` / `write` / `danger` risk tiers preserved. Native-only gaps (`set_wifi`, `tts_offline`) are platform limits, not pending work — see below. |
| Native `run_shell` | shipped (1.5.0) | `/system/bin/sh` as the app uid: no root, no `pkg`/`apt`, toybox subset only. Reported as `restricted` in diagnostics. |
| Native scheduler: jobs + recipes | shipped (1.5.0) | AlarmManager (`setExactAndAllowWhileIdle`, inexact fallback), 5-field cron / interval / one-shot, `battery < N` conditions, re-arm on fire, `BOOT_COMPLETED` replay. |
| Checkpoint / undo for native file tools | shipped (1.5.0) | `write_file` and `delete_file` back up first; `/api/undo` restores; repeat undo rejected. |
| Boot recovery | shipped (1.5.0) | `BootReceiver` restarts the foreground engine and re-arms every alarm after a reboot or app update. |
| Local-first storage | shipped | Python: SQLite at 0600/0700. Native: app-private SQLite (schema v2 with in-place upgrade). |
| SSE streaming chat with per-message token usage | shipped | Both runtimes. |
| Plan / Act agent modes | shipped | Both runtimes (native plan-approve via resend). |
| Agent harness baseline (1.6.x) | shipped | Wall-clock budget, repeated-tool guard, malformed-argument rejection, hard context fit, atomic writes, agent-facing Git/patch tools; Python p40 + native HarnessTest. |
| Native skills directory (1.6.x) | shipped | `filesDir/skills/<name>/SKILL.md` scanning, system-prompt index, on-demand `read_skill`, `/api/skills`; progressive disclosure on both runtimes. |
| Merge checkpoint (1.6.x) | shipped | `refs/pocketmind/premerge/*` pinned before `git merge`; committed merge can be reset, abort clears it; `/api/workspace/git/merge/checkpoint` + `/undo`. |
| Native workspace REST + undo/tasks (1.6.x) | shipped | `info/tree/file/preview/search/mkdir/rename/delete/exec/changes/undo/tasks` on app-private `filesDir`; change log with backups, background tasks with cancel, persisted metadata and live output snapshots; config/db/checkpoint/audit/.git stay hidden; Git via JGit (status/branch/merge/conflict/undo); native PTY uses a small JNI `libpty.so` (arm64/x86_64) with 30s session takeover. |
| Incremental task output (1.6.x) | shipped | Both task managers append stdout/stderr as the process runs; `/tasks/{id}/stream` adds SSE snapshots, and the task panel still polls every 1.5s. |
| Native PRoot / Alpine (1.6.x) | shipped (device test pending) | `libproot.so` + `libtalloc.so` + `libandroid-shmem.so` in `jniLibs/arm64-v8a`, Alpine 3.20 minirootfs in assets; first Linux terminal extracts rootfs to `filesDir/proot`, then `proot -r rootfs -0 -w /workspace /bin/sh` with the current workspace bound to guest `/workspace` (or `-w /root` outside a project). Uses the same PTY transport, 30s takeover separated by shell/proot mode. |
| Three-way block conflict resolution (1.6.x) | shipped | Python `git merge-file --diff3` and native JGit `HistogramDiff` both expose `GET/POST /api/workspace/git/conflicts/blocks*`; choose ours/base/theirs/manual per block, assemble and stage. |
| Runtime permission flow (1.6.x) | shipped | Tool errors carry exact permission names; foreground Activity shows the system dialog; tool card has one-tap grant; background calls fail with `no_activity`. |
| Backup export / import | shipped | `format_version` stamped on both runtimes; bundles are cross-compatible. |
| MCP stdio server / Telegram gateway | Python only | 服务端 stdio 需要外部进程；原生 APK 现在同时支持 HTTP 与经 PRoot 的 stdio **客户端**。 |
| Native MCP HTTP client | shipped | `mcp_client.servers` accepts an `http(s)://…/mcp` URL; initialize → tools/list → tools/call, tools registered as `mcp__server__tool` (danger). |
| Native MCP stdio client (PRoot) | shipped (device test pending) | 非 http(s) 的 command/args 经 `proot -r alpine` 启动，JSON-RPC 按行读写；需要先安装 Linux 开发环境。 |
| PRoot environment manager | shipped (device test pending) | 工作区「Linux 环境」面板：rootfs 状态、minimal/full `apk add`、逐步日志、真实 proot 自检（alpine-release/uid/apk/node/python3/git/gcc/make）。 |
| Native Git clone / pull | shipped | Git 面板直接克隆 http(s)/`file://` 仓库、目标目录校验、拉取 origin；冲突仍进现有三路块面板。 |
| Approval audit view | shipped | `GET /api/approvals/audit` 已是只读 append-only；工作区新增「审计」面板显示最近 80 条。 |
| Read-only native subagent | shipped | `subagent` 工具复用主循环但只允许 `list_dir`/`read_file`，headless 拒绝写/执行，子会话结束即删除。 |
| First-run onboarding wizard | shipped | Provider preset → connectivity-tested API key → permission walkthrough. |
| Session search across tool output | shipped | `matched` kinds on both runtimes (native: LIKE over tool rows). |
| Structured tool result viewer | shipped | List-shaped results render as tables/cards. |
| `mypy` gate | shipped | Zero errors, enforced by `tests/run_all.sh`. |
| Full local gate in one command | shipped | `bash tests/run_all.sh` + `gradle testDebugUnitTest lintDebug assembleDebug` (107 Robolectric tests). |

## Next — 1.7.x

| Item | Status | Notes |
|---|---|---|
| MCP client/server over the native engine | shipped (HTTP + stdio client) | Native client uses Streamable HTTP or stdio over PRoot; hosting an MCP server remains Termux-only. |
| Changeset aggregation | shipped (workspace batch) | The change panel now shows one set with `POST /api/workspace/changes/undo-all`; Agent tool-call checkpoints are still per `tool_call_id`. |

| SSE framing for task output | shipped | Both runtimes expose `/api/workspace/tasks/{id}/stream`; EventSource cannot send Bearer, so the UI keeps polling. |


## Not planned — platform limits

These are not backlog items. They cannot be done as a normal Android app, and
listing them as "pending" would be dishonest.

| Item | Why not | What to do instead |
|---|---|---|
| `set_wifi` in the native engine | Android 10 (API 29) removed the ability for third-party apps to toggle WiFi; `WifiManager.setWifiEnabled` returns false and `NETWORK_SETTINGS` is a system permission. | Use the system quick-settings tile, or the Termux build (which shells out to `termux-wifi-enable`). |
| `tts_offline` (local Chinese TTS model) | Needs a sherpa-onnx model of several hundred MB; the APK is ~9.5 MB with JGit, PRoot and Alpine and the promise is a single small install. | Native `tts_speak` uses the system TTS engine; the Termux build ships the offline model. |
| Root-level shell | `run_shell` runs as the app uid by design; escalating would need a rooted device or a system app. | Use the `run_shell` you have, or a rooted Termux build. |

## Later — beyond 1.7

| Item | Status | Notes |
|---|---|---|
| Local model runtime (llama.cpp / MLC) | exploring | Would remove the cloud-inference dependency for offline use. Not started. |
| Multi-device sync | exploring | Conflicts with the local-first promise unless end-to-end encrypted. Design not started. |
| Desktop companion app | exploring | The MCP server already covers most remote-control needs. |
| iOS | not planned | Termux and `termux-api` have no iOS equivalent; the tool layer would need a full rewrite. |

## Known verification gaps

These are honest statements of what has *not* been proven, kept here so nobody
mistakes a green local gate for full coverage.

| Gap | Why it exists | What would close it |
|---|---|---|
| **The native scheduler has not fired a real alarm on a physical device.** The cron parser, job CRUD, run-now delivery and undo round-trip are covered by Robolectric tests, but `AlarmManager` callbacks, Doze behaviour and `BOOT_COMPLETED` replay are only exercised on real hardware. | The development host is Windows; no Linux runner or working Docker daemon was available. | One green CI run on `ubuntu-latest`, or `bash tests/run_all.sh` inside a Linux container. |
| **No real-device APK test — still the top gap.** The native engine passes 107 Robolectric tests (engine auth, routing, storage, agent loop, approvals, jobs, undo, shell) and the APK builds with lint clean, but no physical device has run the full bootstrap → LLM → tool → approval flow. | The host is a KVM guest without nested virtualisation, so AVDs never come up. | Install v1.7.0 on a physical Android device; exercise onboarding, chat with tool calls, and approval cards. |
| **Playwright E2E is opt-in.** | Browsers are a large download; the gate must stay runnable on a phone. | Run `--with-e2e` on CI as a separate job. |

## Explicitly not planned

- **Play Store distribution.** The app requires `termux-api` permissions and
  arbitrary shell execution; both conflict with store policy.
- **Multi-tenant hosting.** The security model assumes a single user on a single
  device. Running it as a shared service is unsupported.
- **Bypassing approval from the model side.** Approval is enforced server-side by
  design; any change that lets a model self-approve will be rejected.

## How to propose a roadmap change

Open an issue using the feature request template. Describe the user problem, not
the implementation. Roadmap items that do not map to a real user problem are
unlikely to be scheduled.
