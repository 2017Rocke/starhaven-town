# 构建与发布

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

修改 `VERSION`、`CHANGELOG.md` 和 README，测试后重新构建。再次运行新版安装器即覆盖更新；先正常退出旧 app、备份旧应用，再安装新版，失败会尝试恢复旧版。聊天与设置保存在 Application Support，安装器不修改它们。数据库结构变化必须另做迁移、备份及回滚验证。

安装器可在临时目录验收：

```sh
"build/macos-arm64/安装星栖小镇.app/Contents/MacOS/StarhavenInstaller" \
  --install --no-launch --install-root /tmp/starhaven-install-test/Applications \
  --desktop-dir /tmp/starhaven-install-test/Desktop
```

至少测试首次安装、重装更新、数据与皮肤恢复、桌面冲突不覆盖，以及应用退出后无残留后端。当前更新方式是运行新版安装器，没有在线自动下载更新。

## GitHub 发布

将本目录作为独立仓库推送。提交前运行 `python3 scripts/export_source.py`；白名单只包含可公开源文件，并扫描明显个人路径与密钥，不把整个工作区直接压缩。

`ci.yml` 在推送后运行测试；`release.yml` 通过 GitHub Actions 的 Run workflow 手动构建两种 macOS 架构及 Windows x64，结果保存在该次运行的 Artifacts 中。维护者验收后再将安装包上传到 Release，构建流程不会覆盖已经发布的安装包。上传仓库后还需真实运行工作流；本机测试不能代替远端 CI 验收。日常操作见 [GitHub 上传与更新步骤](GITHUB_USAGE.md)。

1.8.0 的发布流程如下：

1. 将已检查的提交推到 `main`，确认 **Checks** 在该提交成功；它会执行 Python 测试、森林与行走脚本语法检查、行走行为测试和公开源码导出。
2. 对同一提交运行 **Build installers**，等待 `macos-15`、`macos-15-intel`、`windows` 三项构建及安装测试全部成功。工作流名称包含提交 SHA，下载前再次核对，避免混入旧版产物。
3. 从 Artifacts 下载 `starhaven-macos-15`、`starhaven-macos-15-intel`、`starhaven-windows-x64`。解开 Actions 外层 ZIP，分别取得两种 DMG、Windows Setup.exe、便携 ZIP、公开源码 ZIP 及其 `.sha256`。
4. 校验每个文件与 SHA-256 配对，并确认版本均为 `VERSION` 中的版本。公开 Release 应有五个安装/源码文件及五个校验文件；使用相同提交创建标签和 Release，先上传草稿并验收附件，再公开发布。

构建包含 `scout_journey.py`、公开的默认居民配置和完整网页资源。全新数据目录应加载六位初始居民，包括领航与星探；已有小镇不会因更新默认配置被覆盖。安装测试使用临时数据，不能用发布者的实际数据替代这项检查。

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

上游 1.5.1 提交 `d0715bf` 记录 Windows `10.0.17763.379` 启动自检通过；本次 1.8.0 更新未在该系统复验，完整功能与安装升级仍待验收。现代 Windows 构建成功不能替代该版本验收。依赖、官方依据与旧系统测试清单见 [WINDOWS](WINDOWS.md)。
