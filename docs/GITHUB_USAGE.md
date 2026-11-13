# 上传源码与发布安装包

仓库：<https://github.com/2017Rocke/starhaven-town>

源码放在仓库的 `main` 分支，安装包放在 **Releases（版本发布）**。普通用户下载 macOS DMG 或 Windows x64 安装 EXE 或便携 ZIP，开发者查看源码或下载源码 ZIP。

## 以后更新源码

在本地项目目录打开终端，完成修改与测试后运行：

```sh
git status
git add README.md web/ server.py
git diff --cached --stat
git commit -m "描述本次改动"
git push origin main
```

`git add` 后的文件只是示例，请替换为本次实际修改的文件。提交前查看暂存清单，确认没有聊天数据、密钥或个人配置。不要使用 `git push --all`；日常只推送 `main` 或当前功能分支。

推送后在仓库 **Actions → Checks** 查看检查结果。修改少量说明文字，也可以在 GitHub 打开文件，点击编辑图标，修改后提交。

## 发布新版安装包

所有开发者遵循同一份 [发布规范](RELEASING.md)。协助发布的智能体使用仓库内的 [starhaven-release 技能](../.agents/skills/starhaven-release/SKILL.md)。

1. 同步远端代码，保留尚未提交的工作；更新 `VERSION`、`CHANGELOG.md`、README 和 `docs/UPDATE_版本.md`，写明本次功能、验证结果与尚未验收的平台。
2. 完成检查，确认公开源码不含个人存档、账号、聊天或密钥。将审核过的文件提交并推送到 `main`，等待该提交的 **Checks** 成功。
3. 在这个提交上创建明确的版本标签，例如 `v1.13.0`，仅推送这一枚标签；不要推送所有本地标签，也不要移动已发布标签。
4. 标签触发 **Build and publish release**：自动构建 Mac Apple 芯片、Mac Intel、Windows x64 安装版和便携版，以及源码包。所有平台构建和安装检查通过后，工作流汇总附件并校验版本、来源提交及 SHA-256。
5. 工作流先准备草稿，确认五个文件与五个校验文件齐全后公开 Release。完成后检查发布页面的版本、提交、十个附件，并实际下载核对校验和。

构建失败不会公开半成品。先查看失败原因；重试方法与已存在草稿的处理见 [发布规范](RELEASING.md)。代码需要修复时创建新提交和新的版本标签，不强推或替换已公开版本的附件。

不要把 DMG、安装 EXE 或便携 ZIP 提交进源码目录，也不要绕过自动流程手动混传不同提交的安装包。普通用户运行新版安装器即可升级，小镇数据继续保留。
