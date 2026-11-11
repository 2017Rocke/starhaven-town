# Windows 安装与兼容性

Windows 发布提供两个附件：

- **`StarhavenTown-版本-windows-x64-Setup.exe`**：推荐。安装到当前用户的 `%LOCALAPPDATA%\Programs\StarhavenTown`，创建桌面和开始菜单快捷方式，无需管理员权限。
- **`StarhavenTown-版本-windows-x64.zip`**：便携备选。完整解压到有写权限的文件夹，双击 `StarhavenTown.exe`；不要单独复制 EXE。支持中文和空格路径。

运行时内置 Python。首次使用 AI 对话，在「小镇设置」连接自己的模型服务。密钥保存在 Windows 凭据管理器。版本未做商业代码签名，系统可能显示“未知发布者”；请从本项目的 GitHub Releases 下载并按附件 SHA-256 校验。

## Windows 10.0.17763.379

此版本属于 **Windows 10 1809 / 17763**。本项目将 **Windows 10 1809 x64** 设为最低目标，面向 Intel / AMD 64 位处理器。安装器和桌面入口都会检查系统版本。32 位 Windows 不受支持。

上游 1.5.1 提交 `d0715bf` 记录 **17763.379 启动自检通过**，包括页面标题、居民加载及退出码。**本次 1.7.0 合并没有在该系统复验，完整功能与安装升级仍待验收。** GitHub 的 `windows-2022` 任务使用较新的 Windows Server，其构建与集成测试也不能代替旧补丁版本验收。微软支持说明要求安装适用的最新累计更新；旧补丁可能影响 WebView2 安装、系统证书及 HTTPS 连接。

依赖及处理方式：

| 组件 | 提供方式与要求 |
| --- | --- |
| Python 3.12 x64 | 内置可重定位运行时。CPython 官方说明最低支持 Windows 8.1；构建实际检查隔离导入、SQLite、TLS、HTTPS 及 app-local VC 运行库。 |
| .NET Framework 4.7.2+ | Windows 10 1809 标准镜像自带。不使用 pythonnet / pywebview。精简系统缺少 .NET 时安装器会提示通过微软官方渠道修复。 |
| Microsoft Edge WebView2 Evergreen x64 | **旧 Windows 10 不保证预装**。缺失时小镇提供微软官方安装页面，用户主动安装后重启小镇；应用不会静默安装系统组件。 |
| Visual C++ 运行库 | Python 所需的 `vcruntime140.dll` 随应用分发。Windows Universal CRT 使用系统组件，不要求用户额外安装 Python。 |

WebView2 缺失时，请访问 [Microsoft 官方下载页](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)，选择 **Evergreen Standalone Installer → x64**。离线电脑可在另一台联网电脑下载此微软安装程序后拷入。不要固定安装已停止安全更新的旧浏览器引擎来规避系统问题。

## 升级、卸载和数据

运行新版本 Setup.exe 可覆盖更新程序。用户数据、聊天记录、设置和 WebView2 缓存默认存放于 `%LOCALAPPDATA%\StarhavenTown`，与安装目录分开；升级、卸载保留用户数据。不可写时可能回退到程序目录的 `data\`，请通过菜单「小镇 → 打开数据文件夹」确认实际位置。便携版更换解压目录时不会自动迁移旧目录中的回退数据，删除旧程序目录前应单独备份这份数据。应用限制同一用户同时运行一份。

升级前可退出小镇并复制整个数据目录备份。应用不会自动在线更新；菜单「下载新版本」打开 GitHub Releases。

启动失败日志位于实际数据目录下的 `logs\desktop-windows.log`，默认完整路径为 `%LOCALAPPDATA%\StarhavenTown\logs\desktop-windows.log`。反馈旧系统兼容性时，请附上 `winver` 版本、CPU 架构、WebView2 安装结果及上述日志；不要附 API 密钥。

## 数据目录与临时目录

默认数据目录是 `%LOCALAPPDATA%\StarhavenTown`。启动时会先探测它是否**真的可写**：受限账户、沙箱化启动或某些域策略下，目录可能已存在但无法创建子目录，仅靠 `Directory.CreateDirectory` 的成功与否判断不出来（它对已存在目录直接返回）。

目录选择规则：

1. 启动参数 `--data-dir` 如有显式指定，即以此为准，失败则报错，不自动回退。
2. 未显式指定时先检查 `%LOCALAPPDATA%\StarhavenTown` 是否可写。
3. 默认目录不可写时尝试程序目录下的 `data\`；仍不可写则报错并提示路径。

运行期需要的临时目录同样处理：`%TEMP%` 不可写时改用数据目录下的 `run-*`，不再启动即崩。

## 旧系统上的兼容模式

针对 Windows 10 1809（17763）上与 Chromium 渲染进程沙箱相关的启动问题（白屏或 `0x80070578 无效的窗口句柄`），当前宿主采用以下兼容策略：

- 支持的最低内部版本 17763 默认附加 `--no-sandbox`；
- 其它版本保持默认隔离，初始化抛异常时自动重试一次兼容模式；
- 程序目录的 `webview2-args.txt` 有非空内容时作为额外参数传给内核。留空或删除恢复按系统版本选择的默认策略，因此 **1809 仍会使用 `--no-sandbox`**。自定义参数也不能阻止初始化失败后的兼容模式重试。

`--no-sandbox` 关闭浏览器内核的进程沙箱。应用仅加载自己的本机页面、采集资料以纯文本展示，这些措施不能替代浏览器沙箱。升级系统后可删除手动添加的兼容参数，让程序优先尝试默认隔离；当前版本没有强制禁止兼容模式重试的开关。

## 随包分发的固定版运行时

程序启动时若在自身目录下发现 `WebView2Runtime\msedgewebview2.exe`，会以微软固定版（Fixed Version）方式直接使用它，不再要求系统安装 WebView2，也不会回退到系统运行时。该目录不随官方安装包提供，需要离线或旧系统可用性时自行放入：从微软 WebView2 页面下载对应架构的 `Microsoft.WebView2.FixedVersionRuntime.*.cab`，解压后把含 `msedgewebview2.exe` 的那一层改名为 `WebView2Runtime` 放在 `StarhavenTown.exe` 旁边。

## 构建与验证

在 Windows x64 安装 Python 3.12 可重定位运行时、.NET Framework 构建工具和 [Inno Setup 6](https://jrsoftware.org/isdl.php)，运行：

```powershell
python scripts/build_windows.py --python-runtime "C:\path\to\relocatable-python"
```

构建生成原生 `StarhavenTown.exe`、安装 EXE、便携 ZIP 及各自 SHA-256。WebView2 SDK 固定为 **1.0.3537.50**，从官方 NuGet 获取并验证 SHA-256；仅分发 `net462` 程序集及 **x64 WebView2Loader.dll**，许可证随安装包保留。框架目标为 .NET Framework 4.7.2，窗口以当前用户权限运行。

GitHub Actions → **Build installers** → **Run workflow** 可构建两个 macOS 架构和 Windows x64。Windows 原生集成测试仅在可丢弃测试账号中执行，会创建该账号的快捷方式及卸载信息。自测用临时中文目录，验证网页实际加载、SQLite 数据、重复启动保留文件、关闭后端口释放、安装升级和卸载数据保留。

针对 1.7.0 在 17763.379 上的待验收项目：安装与桌面图标、缺失 WebView2 的提示和官方安装流程、启动与拖拽地图、居民创建和聊天、观察站采集与资料展示、HTTPS 模型连接、退出无后台残留、覆盖升级保留数据。未完成该系统验收前，请把完整兼容性视为待确认。

## 官方依据

- [CPython 3.12 Windows 支持范围](https://docs.python.org/3.12/using/windows.html)
- [Microsoft .NET Framework 版本与系统依赖](https://learn.microsoft.com/en-us/dotnet/framework/migration-guide/versions-and-dependencies)：Windows 10 1809 内置 4.7.2。
- [Microsoft WebView2 分发要求](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)：应用必须检测运行时，旧 Windows 10 并非全部预装。
- [Microsoft Edge 支持的操作系统](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-supported-operating-systems)：包括 Windows 10 SAC 1709+、Enterprise 2019 LTSC，同时要求适用累计更新。
- [WebView2 SDK 1.0.3537.50](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3537.50)：使用包内 `net462` 程序集。
