# 构建与发布

本文是所有开发者统一使用的发布手册。智能体从仓库 [starhaven-release 技能](../.agents/skills/starhaven-release/SKILL.md) 进入；根目录 [AGENTS.md](../AGENTS.md) 与 [CONTRIBUTING.md](../CONTRIBUTING.md) 引用本手册，不另维护第二套发布步骤。发布准备不自动授权推送或公开 Release；已有发布授权沿用到该次发布的构建、发布与核验。

## 本机构建

### macOS


需要 macOS、Apple Command Line Tools 和来源可信的**可重定位 Python 发行包**。推荐 [python-build-standalone](https://github.com/astral-sh/python-build-standalone) 或 `uv python install 3.12` 安装的托管 Python。依赖本机 Framework 的普通 Python 不适合嵌入分发。

```sh
uv python install 3.12
runtime_root="$(uv run --python 3.12 --no-project python -c 'import sys; print(sys.base_prefix)')"
python3 scripts/build_macos.py --python-runtime "$runtime_root"
python3 scripts/export_source.py
```

`runtime_root` 包含 `bin/python3` 和 `lib`。构建只使用标准库、Swift、codesign、iconutil 和 hdiutil，无需 pip 包。不同芯片分别在对应 Mac 构建；当前本地验证为 Apple 芯片。

输出 `dist/StarhavenTown-版本-macOS-架构.dmg`、校验和，以及 `starhaven-town-版本-source.zip`。开发用 app 位于 `build/macos-架构/`。安装包含运行环境，源码包需要用户自行安装 Python。

源码、网页、公开初始居民配置 `defaults/residents.json` 和内置 runtime 进入 app；个人模型账号、数据库、团队配置不进入 app。Mac 服务使用随机本机端口，由桌面窗口自动加载，不提供固定浏览器入口。

## 版本与更新

同步修改 `VERSION`、`docs/UPDATE_<版本>.md`、`CHANGELOG.md` 和 README，测试后重新构建。更新说明文件的首行及变更记录的最新版本必须与 `VERSION` 一致；GitHub Release 的正文直接读取该更新说明文件。再次运行新版安装器即覆盖更新；先正常退出旧 app、备份旧应用，再安装新版，失败会尝试恢复旧版。聊天与设置保存在 Application Support，安装器不修改它们。数据库结构变化必须另做迁移、备份及回滚验证。

安装器可在临时目录验收：

```sh
"build/macos-arm64/安装星栖小镇.app/Contents/MacOS/StarhavenInstaller" \
  --install --no-launch --install-root /tmp/starhaven-install-test/Applications \
  --desktop-dir /tmp/starhaven-install-test/Desktop
```

至少测试首次安装、重装更新、数据与皮肤恢复、桌面冲突不覆盖，以及应用退出后无残留后端。当前更新方式是运行新版安装器，没有在线自动下载更新。

## GitHub 发布

发布必须来自已验证提交的不可变 `vMAJOR.MINOR.PATCH` 标签。不要直接从移动中的分支、旧工作流或本地残留 `dist/` 发布安装包，也不要移动、删除重建已有版本标签。

1. 核对分支、工作区和远端，保留其他人的提交与未提交工作。同步远端时先检查差异；历史标签冲突须单独处理，不强制覆盖标签。必要时在独立工作树中整合，完成相关回归与平台验证。
2. 同步版本文档，运行 `python3 scripts/export_source.py` 检查公开源码白名单。提交全部发布改动，确认工作区干净，再推送该提交到 `main`。等待 **Checks** 对这个确切 SHA 完整成功；本机检查不能代替远端 CI。
3. 从同一提交创建并预检本地标签，确认远端尚无同名标签后，只推送本次标签。例如在已验证提交上执行：

   ```sh
   release_version="$(cat VERSION)"
   release_tag="v${release_version}"
   git tag -a "$release_tag" HEAD -m "Starhaven Town $release_version"
   python3 scripts/check_release.py preflight --tag "$release_tag"
   git push origin "refs/tags/$release_tag"
   ```

   预检验证标签、当前提交、`VERSION`、更新说明首行和最新 CHANGELOG 条目，并拒绝未提交或未跟踪改动；忽略的 `build/`、`dist/` 产物可保留。标签已存在时核对其 commit，不能重建来绕过检查。
4. 标签推送自动触发 **Build and publish release**（`.github/workflows/release.yml`）。预检解析固定 commit SHA；两种 Mac 与 Windows 构建随后都检出该 SHA。Mac `arm64`、`x86_64` 和 Windows `x64` 分别完成现有 Python、前端、构建与安装集成检查，成功后才保存平台产物。
5. 三个平台全部成功后，唯一拥有 `contents: write` 的发布 job 开始工作。它检查平台清单中的标签/版本/SHA、每个载荷的 SHA-256、三平台源码 ZIP 的字节一致性及十个必需附件；再通过 `actions: read` 查询该 SHA 的 **Checks** 成功结果。任一检查失败，都不会公开 Release。
6. 发布 job 先创建或补齐该标签的草稿，核对 GitHub 返回的全部附件 digest、大小与数量，再公开 Release。发布说明直接使用该提交的 `docs/UPDATE_<版本>.md`。公开后重新下载全部十个附件，核验文件集合和 SHA-256，生成发布证据。

### 必需附件与证据

每个版本必须恰有以下五个文件及各自同名的 `.sha256`，共 **10 个附件**：

| 平台 | 文件名（将 `<版本>` 替换为 `VERSION`） |
| --- | --- |
| Apple Silicon Mac | `StarhavenTown-<版本>-macOS-arm64.dmg` |
| Intel Mac | `StarhavenTown-<版本>-macOS-x86_64.dmg` |
| Windows 安装器 | `StarhavenTown-<版本>-windows-x64-Setup.exe` |
| Windows 便携版 | `StarhavenTown-<版本>-windows-x64.zip` |
| 公开源码 | `starhaven-town-<版本>-source.zip` |

平台构建 Artifacts 分别为 `starhaven-macos-arm64`、`starhaven-macos-x86_64`、`starhaven-windows-x64`。构建清单 `release-build.json` 与 `source-files.txt` 只用于校验，不上传为 Release 附件。源码导出固定文件顺序、ZIP 时间与权限，统一文本换行，并使用不依赖压缩库版本的 ZIP 存储方式；包含根 `AGENTS.md` 和 `.agents/skills/starhaven-release/`，不收录其他本地技能、个人配置或整个工作区。

成功发布后，从运行的 `release-verification-<版本>` Artifact 取回 `release-<版本>-verification.json`，保存到本地忽略目录 `build/`，并更新维护者已有交接摘要。证据记录版本、tag/commit SHA、Checks 与构建 run、Release 地址、十个文件的下载校验及未验收事项。确认远端 tag SHA 与证据一致；CI 已完成整套下载校验时无需在本机重复下载全部安装包。不要把机器生成证据或个人交接文件打入公开源码。

### 失败与重试

- 构建或上传遇到暂时故障且源码无需修改时，优先使用原运行的 **Re-run failed jobs**，重用同一提交及已成功构建的产物。需要重新构建时，可在 `main` 上手动运行 **Build and publish release**，输入既有的完整标签名；工作流仍检出标签对应的固定 SHA。同一标签的运行串行执行，不取消正在进行的发布。
- 草稿中已有附件只有与本次产物完全一致才允许继续，流程只补缺失项。既有 Release 已公开时，正文、真实标签解引用得到的 commit SHA 和全部附件必须完全一致才视为幂等成功；`target_commitish` 等描述性元数据不能代替真实标签核验。任何同名不同内容、额外附件或归属冲突都会停止，不执行删除、替换或 `--clobber`。重新构建的安装包可能因签名时间等原因改变字节，此时应使用原运行产物恢复，不能覆盖旧附件。
- 需要修改源码或工作流才能修复时，创建新提交；已经创建的版本标签保持不动，改用新的版本号与新标签发布。已经公开的版本同样通过新版本修复，不删除 Release 来隐藏失败记录。
- 若 Release 已公开但下载核验失败，保留公开附件，重试原发布 job 的核验；取得完整证据前不能报告发布验收完成。证据文件只在下载校验成功后生成。

构建平台的成功范围按实际环境说明：`windows-2022` 不等于 Windows 10 `17763.379` 实机验收，默认 Mac 产物也不代表已完成 Apple 公证。日常 GitHub 操作见 [上传与更新步骤](GITHUB_USAGE.md)。

构建包含 `scout_journey.py`、公开的默认居民配置和完整网页资源。全新数据目录应加载八位初始居民，包括领航、星探和两位警官；已有小镇不会因更新默认配置被覆盖；两位警官按稳定编号补入空闲地块。安装测试使用临时数据，不能用发布者的实际数据替代这项检查。

## Apple 签名公证

默认 `--identity -` 是 ad-hoc 签名，只校验包完整性。公开分发若希望用户下载后无开发者验证拦截，需要发布者自己的 Apple Developer ID 证书与公证账号。项目不带证书、不移除 quarantine、不绕过 Gatekeeper。

证书已在构建机安装时：

```sh
python3 scripts/build_macos.py --python-runtime "$runtime_root" \
  --identity "Developer ID Application: YOUR ORGANIZATION (TEAMID)"
xcrun notarytool submit dist/StarhavenTown-VERSION-macOS-arm64.dmg \
  --keychain-profile starhaven-notary --wait
xcrun stapler staple dist/StarhavenTown-VERSION-macOS-arm64.dmg
```

替换版本、架构与身份。公证完成后重新计算 DMG 的 SHA-256。自动工作流默认只构建预览包，未声称已完成签名公证。未来在线升级需验证可信发布源、签名、失败回滚及用户控制。


### Windows 10 1809 x64

在 **64 位 Windows** 上构建（本机 Intel/AMD 或 GitHub `windows-2022`）。需要 .NET Framework 编译器、Inno Setup 6 和可重定位 Python 3.12；构建脚本从微软官方 NuGet 获取固定版本 WebView2 SDK 并校验 SHA-256。

```powershell
uv python install 3.12
$runtime = uv run --python 3.12 --no-project python -c "import sys; print(sys.base_prefix)"
uv run --python 3.12 --no-project python scripts/build_windows.py --python-runtime $runtime
uv run --python 3.12 --no-project python scripts/export_source.py
```

输出 `dist/StarhavenTown-版本-windows-x64-Setup.exe`、便携 `.zip` 和各自 `.sha256`。包内含 Python、原生 `StarhavenTown.exe`、WebView2 SDK 组件及许可。**官方构建不包含 Evergreen 或 Fixed Version 浏览器运行时**；用户可另行安装微软 Evergreen x64，宿主也支持放在程序旁的 `WebView2Runtime` 固定版。固定版部署与更新由维护者负责。标准 Windows 10 1809 自带 .NET Framework 4.7.2。

构建后在独立 Windows 测试账号中执行 `tests/test_windows_integration.py`，设置 `STARHAVEN_WINDOWS_APP` 和 `STARHAVEN_WINDOWS_INSTALLER` 为刚生成的 EXE 路径。测试会创建快捷方式和卸载注册信息，不能在正在使用的小镇账号下随意运行。GitHub 工作流自动完成该检查，分别验证原生窗口、实际地图加载、中文路径、退出清理及安装升级/卸载保留数据。

上游 1.5.1 提交 `d0715bf` 记录 Windows `10.0.17763.379` 启动自检通过；当前版本未在该系统完整复验，完整功能与安装升级仍待验收。现代 Windows 构建成功不能替代该版本验收。依赖、官方依据与旧系统测试清单见 [WINDOWS](WINDOWS.md)。
