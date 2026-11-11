# 星栖小镇 · Starhaven Town

一个可以与智能体居民聊天、记录成长、观察团队工作的像素小镇。每位居民都有自己的名字、性格、简介、明确记忆和独立聊天记录。当前版本：**1.7.0**。

**面向日常使用：桌面版。** 支持 macOS 与 Windows 10 x64（Intel/AMD）。不再维护“在浏览器里开固定端口网页”的使用方式。

当前提供可拖动探索的大地图、领航与星探、自定义居民和专属房屋、真实模型对话、任务板、成长册，以及「暖绿原野」「星夜营地」两套皮肤。小镇最多容纳 30 位居民，房屋按职业、性格与简介生成，空地保留果园、菜地、花田和池塘。应用和个人数据分开保存，方便后续更新。

星探的观察站支持真实 GitHub 公开项目采集：点击「让星探出发」，寻找项目、取回固定版本源码并整理到材料架。可以查看来源、许可证、作者公开信息、文件目录和风险线索；室内书架展示已有研究报告。每日采集可在观察站启用，仅在小镇运行时执行。详见 [星探的观察站](docs/SCOUT.md)。

[下载 Mac / Windows 版本](https://github.com/2017Rocke/starhaven-town/releases/latest) · [查看源码](https://github.com/2017Rocke/starhaven-town) · [如何上传与发布新版](docs/GITHUB_USAGE.md)

## 打开桌面版

| 方式 | 说明 |
| --- | --- |
| macOS `.dmg` | Apple 芯片选 `arm64`，Intel 芯片选 `x86_64`；打开后运行「安装星栖小镇」 |
| Windows `Setup.exe` | Windows 10 1809（17763）及以上 x64；按向导安装并创建桌面图标 |
| Windows `.zip` | 便携版，完整解压后运行应用；个人数据仍保存到独立数据目录 |
| 源码 ZIP | 用于开发与构建，不是桌面安装包 |

需要 macOS 13+。当前本机验证目标为 Apple Silicon（arm64）。安装包是本地 ad-hoc 签名预览版时，跨电脑下载可能被系统拦截；面向公众发布需完成 Developer ID 签名与公证。

Windows 版本针对 **10.0.17763.379（Windows 10 1809）** 的系统能力设计，使用系统 .NET Framework 4.7.2 和 Microsoft Edge WebView2。旧系统可能没有 WebView2，应用会提供官方安装入口。GitHub 的 Windows 构建与验收使用较新的系统，**不等于在 17763.379 真机上通过测试**。具体依赖、安装和排查步骤见 [Windows 使用说明](docs/WINDOWS.md)。

安装后应用位于 `~/Applications/星栖小镇.app`。红叉隐藏窗口；菜单「退出星栖小镇」或 `⌘Q` 完整退出。更新时运行新版安装器，个人资料与聊天保留。桌面图标是应用的符号链接，删图标不会删应用或数据。

## 连接 AI

打开页面右上角「小镇设置」，或 macOS 菜单「设置…」。

- **自选模型 API**：OpenAI 兼容 `chat/completions`，地址通常以 `/v1` 结尾，填写模型名与密钥
- **本机模型**：例如 `http://127.0.0.1:11434/v1`，填已安装模型名；可无密钥
- **Codex**：使用本机已有 Codex 配置（应用不含 Codex，也不提供共享额度）

远程地址必须 HTTPS；HTTP 仅允许本机。macOS 密钥进钥匙串，Windows 密钥进凭据管理器，都不写进项目文件。费用由你自己的模型账户承担。未连接模型时仍可看小镇、改资料、换皮肤、读已有记录。

## 个人数据与团队连接

桌面数据目录：

- macOS：`~/Library/Application Support/StarhavenTown/`
- Windows：`%LOCALAPPDATA%\StarhavenTown\`

菜单「打开数据文件夹」可直接查看。居民、记忆、聊天与偏好在 SQLite 中；更新应用不会替换该目录。备份前先退出，再复制整个目录。

菜单「连接团队目录…」可选择含 `config.json` 与 `state.json` 的协调官目录。小镇只读进度与成果，不自动改写台账、不派单。新增居民只是对话角色，不会自动成为执行成员。

## 开发与构建（非日常使用）

仓库里的 `server.py`、`web/`、`desktop_runner.py` 是 **桌面应用内嵌后端与页面**，以及测试/打包用的工程文件，不是给最终用户开浏览器访问的产品入口。

```sh
python3 -m unittest discover -s tests -v
```

桌面构建、签名与发布见 [RELEASING](docs/RELEASING.md)。架构与扩展点见 [ARCHITECTURE](docs/ARCHITECTURE.md)。贡献约定见 [CONTRIBUTING](CONTRIBUTING.md)。

## 文档

- [1.7.0 功能迭代说明](docs/UPDATE_1.7.0.md) · [星探的观察站](docs/SCOUT.md)
- [使用说明](使用说明.md)（日常）
- [Windows 安装与旧系统兼容性](docs/WINDOWS.md)
- [架构](docs/ARCHITECTURE.md) · [皮肤](docs/SKINS.md) · [团队台账](docs/TEAM_DATA.md)
- [发布](docs/RELEASING.md) · [GitHub 用法](docs/GITHUB_USAGE.md)
- [CHANGELOG](CHANGELOG.md) · [ROADMAP](ROADMAP.md)
- [LICENSE](LICENSE) · [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)
