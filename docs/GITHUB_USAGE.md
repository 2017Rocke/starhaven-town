# 上传源码与发布安装包

仓库：<https://github.com/2017Rocke/starhaven-town>

源码放在仓库的 `main` 分支，安装包放在 **Releases（版本发布）**。普通用户下载 DMG，开发者查看源码或下载源码 ZIP。

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

1. 修改 `VERSION` 与 `CHANGELOG.md`，完成测试，再按 [构建指南](RELEASING.md) 生成新版 DMG、源码 ZIP 和 SHA-256 校验文件。
2. 将本次源码推送到 `main`。
3. 在仓库右侧点击 **Releases → Draft a new release**。
4. 在 **Choose a tag** 填新版本号，例如 `v1.1.1`，选择创建标签，目标分支选 `main`。
5. 填标题与更新说明，将 DMG、源码 ZIP 及对应 `.sha256` 文件拖到附件区域。
6. 等待附件上传完毕，检查版本与文件名称。未完成正式签名公证的预览包应在说明中写清楚；确认后点击 **Publish release**。

不要把 DMG 提交进源码目录。已有版本的附件保留，后续更新创建新版本，便于下载与回退。普通用户运行新版安装器即可升级，小镇数据继续保留。

也可在 **Actions → Build macOS installers → Run workflow** 手动生成构建产物，下载 Artifacts 并验收后再发布；这一步不会自动公开或替换 Release 附件。
