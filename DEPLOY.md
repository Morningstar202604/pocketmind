# 部署指南（口袋智灵）

本项目可一键部署为公网可访问的网站（Web UI + agentd 后端一体，前端 dist 内置，无需 Node）。

## 方式一：本机/VPS 一键部署（推荐）

```bash
git clone https://github.com/X33834/termux-agent && cd termux-agent
# root 用户：
bash deploy/deploy.sh
# 非 root：同样执行，自动走 nohup 分支（nginx 反代需 root，非 root 跳过）
```

脚本自动完成：装依赖 → 初始化数据目录（700）→ 生成随机访问令牌 → 以 `--lan` 模式常驻启动 → nginx 反代 80 端口。

**部署后：**
- 访问 `http://<服务器IP>/`（80 端口）
- 首次打开页面输入访问令牌（脚本输出的那串，或页面 ⚙ 设置里查看/修改）
- ⚙ 设置 → 选 LLM 厂商 → 填 API Key → 保存 → 开聊

## 方式二：Docker（任一有 Docker 的机器）

```bash
# 1. 设置访问令牌（必填，公网安全）
export AGENT_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
# 2. 启动
docker compose up -d
# 3. 访问 http://<服务器IP>:8787
```

## 方式三：手动（简单验证）

```bash
python3 -m pip install -r agentd/requirements.txt
# 生成令牌：首次打开页面时设置即可
python3 -m agentd.main --lan --port 8787
# 访问 http://127.0.0.1:8787（本机）或 http://<IP>:8787（局域网/公网）
```

## 生产环境建议

| 项 | 建议 |
|---|---|
| HTTPS | nginx 用 certbot（Let's Encrypt）或云厂商证书；docker 部署可前置 Caddy 自动 HTTPS |
| 访问令牌 | 强令牌（脚本已生成 48 位随机）；页面设置里可随时改 |
| 数据备份 | `AGENT_HOME`（默认 `~/.agent/termux-agent`）整目录定期备份，含会话/记忆/定时任务/checkpoints |
| 资源 | 单进程即可支撑个人使用；内存约 150-300MB（Python+SQLite） |
| 安全边界 | LLM 输出在页面经 DOMPurify 消毒；服务端危险工具需审批；`--lan` 必须配令牌 |

## 服务器侧代理（可选）

仓库提供现成 nginx 配置：`deploy/nginx/agentd.conf`（已关闭 SSE 缓冲，`proxy_buffering off`）。
