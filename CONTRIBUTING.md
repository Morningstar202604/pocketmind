# 贡献指南

感谢你对 **PocketMind（口袋智灵）** 的关注！欢迎以任何形式参与贡献：提 Issue、修 Bug、写文档、加功能都可以。

## 快速开始

1. Fork 本仓库到你的账号
2. 从 `main` 创建功能分支：`git checkout -b feat/your-feature`
3. 本地开发与运行方式见 [README](README.md)
4. 提交改动并发起 Pull Request，描述清楚改了什么、为什么改

## 提交规范

- 提交信息建议遵循 Conventional Commits：`feat: 新增xx`、`fix: 修复xx`、`docs: 文档`、`chore: 杂务`
- 每个提交聚焦单一改动，便于回溯与回滚
- 不要把密钥、个人数据提交进仓库

## 问题反馈

- 提 Issue 前请先搜索是否已有同类问题
- Bug 请附上复现步骤、预期/实际行为、环境信息
- 功能建议请说明使用场景

## 行为准则

参与贡献即表示同意遵守 [贡献者行为准则](CODE_OF_CONDUCT.md)。

## 发布流程（维护者）

每次对外更新按此清单执行，不跳步：

1. **定版本**：按语义化版本（SemVer）——修 Bug 升补丁位、新功能升次版本、破坏性改动升主版本
2. **同步文档**：改代码的同时更新 README（版本/截图，如界面有变）与依赖清单（requirements / package.json）
3. **本地验证**：`bash tests/run_all.sh` 必须全绿（全部后端套件 + compileall/ruff/部署自检 + 前端
   typecheck/单测/构建/dist 新鲜度）；需要 E2E 时加 `--with-e2e`
4. **合并**：以 PR 方式合入 `main`，门禁全绿后合并
5. **打标签**：`git tag -a vX.Y.Z -m "vX.Y.Z"` 并推送（`git push origin vX.Y.Z`）
6. **同步镜像**：主仓库在 GitCode（`gitcode.com/badhope/pocketmind`），镜像在 Gitee
   （`gitee.com/badhope/pocketmind`）与 GitHub（`github.com/X33834/pocketmind`）。
   克隆默认只有 `origin`，先各加一个远程再推，保持三处内容一致：
   `git remote add gitee https://gitee.com/badhope/pocketmind`、
   `git remote add github https://github.com/X33834/pocketmind`，
   然后 `git push gitee main --tags`、`git push github main --tags`
7. **发布**：基于该 tag 在镜像仓库创建 Release，Release Notes 写清本次变更要点
   （官网 `website/index.html` 为自包含单页，随仓库静态托管，无独立的 Pages 流水线）

变更记录以 [CHANGELOG.md](CHANGELOG.md)、Releases 与 git log 为准。
