import AppKit
import WebKit
import Foundation
import Darwin

/// Native lifetime owner for the bundled local service. User data never lives in the app bundle.
final class TownAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private let appName = "星栖小镇"
    private let bundleID = "cn.starhaven.town"
    private let fileManager = FileManager.default
    private var mainWindow: NSWindow!
    private var webView: WKWebView!
    private var statusView: NSView!
    private var statusTitle: NSTextField!
    private var statusDetail: NSTextField!
    private var spinner: NSProgressIndicator!
    private var retryButton: NSButton!
    private var backend: Process?
    private var logHandle: FileHandle?
    private var readyDirectory: URL?
    private var readyTimer: Timer?
    private var attemptID = UUID()
    private var origin: URL?
    private var quitting = false
    private var reportWindows: [NSWindow] = []

    private var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.1.0"
    }

    private var dataDirectory: URL {
        fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("StarhavenTown", isDirectory: true)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let current = NSRunningApplication.current
        let earlier = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .filter { $0.processIdentifier != current.processIdentifier && !$0.isTerminated }
            .filter { ($0.launchDate ?? .distantPast) <= (current.launchDate ?? Date()) }
            .sorted { ($0.launchDate ?? .distantPast) < ($1.launchDate ?? .distantPast) }
        if let existing = earlier.first {
            existing.activate(options: [.activateAllWindows])
            NSApp.terminate(nil)
            return
        }
        buildMenu()
        buildWindow()
        showWindow()
        startBackend()
    }

    private func buildMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: appName)
        appItem.submenu = appMenu
        appMenu.addItem(menuItem("关于星栖小镇", #selector(showAbout), ""))
        appMenu.addItem(menuItem("安装与更新说明…", #selector(showUpdateHelp), ""))
        appMenu.addItem(.separator())
        appMenu.addItem(menuItem("设置…", #selector(showSettings), ","))
        appMenu.addItem(menuItem("连接团队目录…", #selector(selectTeamDirectory), ""))
        appMenu.addItem(menuItem("打开数据文件夹", #selector(openDataDirectory), ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "隐藏星栖小镇", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        let hideOthers = NSMenuItem(title: "隐藏其他应用", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(hideOthers)
        appMenu.addItem(NSMenuItem(title: "显示所有应用", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "退出星栖小镇", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        bar.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "编辑")
        editItem.submenu = editMenu
        editMenu.addItem(NSMenuItem(title: "撤销", action: Selector(("undo:")), keyEquivalent: "z"))
        let redo = NSMenuItem(title: "重做", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(redo)
        editMenu.addItem(.separator())
        for (title, action, key) in [("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            editMenu.addItem(NSMenuItem(title: title, action: Selector(action), keyEquivalent: key))
        }
        bar.addItem(editItem)

        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "窗口")
        viewItem.submenu = viewMenu
        viewMenu.addItem(menuItem("回到小镇", #selector(returnToTown), "1"))
        viewMenu.addItem(menuItem("重新加载", #selector(reloadTown), "r"))
        viewMenu.addItem(NSMenuItem(title: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"))
        viewMenu.addItem(NSMenuItem(title: "缩放", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: ""))
        bar.addItem(viewItem)
        NSApp.mainMenu = bar
        NSApp.windowsMenu = viewMenu
    }

    private func menuItem(_ title: String, _ action: Selector, _ key: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        return item
    }

    private func buildWindow() {
        mainWindow = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 850),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        mainWindow.title = appName
        mainWindow.minSize = NSSize(width: 1000, height: 700)
        mainWindow.isReleasedWhenClosed = false
        mainWindow.delegate = self
        mainWindow.setFrameAutosaveName("StarhavenTownMainWindow")
        mainWindow.center()
        let container = NSView()
        mainWindow.contentView = container

        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            webView.topAnchor.constraint(equalTo: container.topAnchor),
            webView.bottomAnchor.constraint(equalTo: container.bottomAnchor)
        ])

        statusView = NSView()
        statusView.wantsLayer = true
        statusView.layer?.backgroundColor = NSColor(calibratedRed: 0.97, green: 0.95, blue: 0.89, alpha: 1).cgColor
        statusView.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(statusView)
        NSLayoutConstraint.activate([
            statusView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            statusView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            statusView.topAnchor.constraint(equalTo: container.topAnchor),
            statusView.bottomAnchor.constraint(equalTo: container.bottomAnchor)
        ])
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 18
        stack.translatesAutoresizingMaskIntoConstraints = false
        let icon = NSTextField(labelWithString: "✦")
        icon.font = .systemFont(ofSize: 64, weight: .semibold)
        icon.textColor = NSColor(calibratedRed: 0.36, green: 0.49, blue: 0.31, alpha: 1)
        statusTitle = NSTextField(labelWithString: "小镇正在醒来")
        statusTitle.font = .systemFont(ofSize: 25, weight: .semibold)
        statusTitle.textColor = .labelColor
        statusDetail = NSTextField(wrappingLabelWithString: "正在准备居民与记忆，请稍候。")
        statusDetail.font = .systemFont(ofSize: 15)
        statusDetail.textColor = .secondaryLabelColor
        statusDetail.alignment = .center
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .regular
        spinner.isIndeterminate = true
        retryButton = NSButton(title: "重新打开小镇", target: self, action: #selector(retryStartup))
        retryButton.bezelStyle = .rounded
        retryButton.isHidden = true
        for item in [icon, statusTitle!, statusDetail!, spinner!, retryButton!] as [NSView] {
            stack.addArrangedSubview(item)
        }
        statusView.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: statusView.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: statusView.centerYAnchor, constant: -20),
            stack.widthAnchor.constraint(equalToConstant: 560),
            statusDetail.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
    }

    private func showWindow() {
        guard mainWindow != nil else { return }
        if mainWindow.isMiniaturized { mainWindow.deminiaturize(nil) }
        mainWindow.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func startBackend() {
        guard !quitting else { return }
        attemptID = UUID()
        let thisAttempt = attemptID
        origin = nil
        statusView.isHidden = false
        retryButton.isHidden = true
        retryButton.isEnabled = true
        statusTitle.stringValue = "小镇正在醒来"
        statusDetail.stringValue = "正在准备居民与记忆，请稍候。"
        spinner.isHidden = false
        spinner.startAnimation(nil)

        do {
            try fileManager.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
            let logDirectory = dataDirectory.appendingPathComponent("logs", isDirectory: true)
            try fileManager.createDirectory(at: logDirectory, withIntermediateDirectories: true)
            let logURL = logDirectory.appendingPathComponent("desktop.log")
            if !fileManager.fileExists(atPath: logURL.path) {
                fileManager.createFile(atPath: logURL.path, contents: nil)
            }
            let handle = try FileHandle(forWritingTo: logURL)
            handle.seekToEndOfFile()
            logHandle = handle
            let temporary = fileManager.temporaryDirectory.appendingPathComponent("starhaven-\(UUID().uuidString)", isDirectory: true)
            try fileManager.createDirectory(at: temporary, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
            readyDirectory = temporary
            let readyURL = temporary.appendingPathComponent("ready.json")
            guard let resources = Bundle.main.resourceURL else {
                throw NSError(domain: bundleID, code: 1, userInfo: [NSLocalizedDescriptionKey: "应用资源不完整，请重新安装星栖小镇。"])
            }
            let executable = resources.appendingPathComponent("backend/StarhavenBackend")
            guard fileManager.isExecutableFile(atPath: executable.path) else {
                throw NSError(domain: bundleID, code: 2, userInfo: [NSLocalizedDescriptionKey: "没有找到小镇运行组件，请重新运行安装器。你的居民和记忆会保留。"])
            }
            let process = Process()
            process.executableURL = executable
            process.arguments = ["--data-dir", dataDirectory.path,
                                 "--ready-file", readyURL.path,
                                 "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
            process.currentDirectoryURL = dataDirectory
            var environment = ProcessInfo.processInfo.environment
            environment["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (environment["PATH"] ?? "")
            environment.removeValue(forKey: "PYTHONHOME")
            environment.removeValue(forKey: "PYTHONPATH")
            process.environment = environment
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = handle
            process.standardError = handle
            process.terminationHandler = { [weak self] stopped in
                DispatchQueue.main.async {
                    guard let self = self, self.attemptID == thisAttempt, !self.quitting,
                          self.backend === stopped else { return }
                    self.showStartupError("小镇服务已停止（状态 \(stopped.terminationStatus)）。请重新打开；若仍无法启动，可在“打开数据文件夹”的 logs 中查看记录。")
                }
            }
            backend = process
            try process.run()
            let deadline = Date().addingTimeInterval(60)
            readyTimer?.invalidate()
            readyTimer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self, weak process] timer in
                guard let self = self, let process = process, self.attemptID == thisAttempt, !self.quitting else {
                    timer.invalidate()
                    return
                }
                if let bytes = try? Data(contentsOf: readyURL),
                   let ready = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
                   let port = ready["port"] as? Int,
                   let pid = ready["pid"] as? Int,
                   (1...65535).contains(port), pid == Int(process.processIdentifier),
                   let url = URL(string: "http://127.0.0.1:\(port)/") {
                    timer.invalidate()
                    self.readyTimer = nil
                    self.origin = url
                    self.statusDetail.stringValue = "居民们已准备好，正在进入小镇。"
                    self.webView.load(URLRequest(url: url))
                    self.removeReadyDirectory()
                } else if Date() >= deadline {
                    timer.invalidate()
                    self.showStartupError("小镇启动时间较长。请重新打开；你的居民资料和聊天记录会保留。")
                    self.retryButton.isEnabled = false
                    self.stopBackend { self.retryButton.isEnabled = true }
                }
            }
        } catch {
            showStartupError(error.localizedDescription)
            retryButton.isEnabled = false
            stopBackend { self.retryButton.isEnabled = true }
        }
    }

    private func showStartupError(_ message: String) {
        readyTimer?.invalidate()
        readyTimer = nil
        statusView.isHidden = false
        statusTitle.stringValue = "小镇暂时没有打开"
        statusDetail.stringValue = message
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        retryButton.isHidden = false
    }

    /// Only this Process instance is terminated. Existing command-line town services are untouched.
    private func stopBackend(completion: @escaping () -> Void) {
        readyTimer?.invalidate()
        readyTimer = nil
        let owned = backend
        let ownedLog = logHandle
        backend = nil
        logHandle = nil
        owned?.terminationHandler = nil
        removeReadyDirectory()
        guard let process = owned, process.isRunning else {
            try? ownedLog?.close()
            completion()
            return
        }
        process.terminate()
        DispatchQueue.global(qos: .utility).async {
            // Let the backend cancel model processes and commit interrupted jobs before exiting.
            let deadline = Date().addingTimeInterval(20)
            while process.isRunning && Date() < deadline {
                Thread.sleep(forTimeInterval: 0.05)
            }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
            try? ownedLog?.close()
            DispatchQueue.main.async { completion() }
        }
    }

    private func removeReadyDirectory() {
        if let directory = readyDirectory { try? fileManager.removeItem(at: directory) }
        readyDirectory = nil
    }

    @objc private func retryStartup() {
        retryButton.isEnabled = false
        attemptID = UUID()
        stopBackend { [weak self] in
            self?.retryButton.isEnabled = true
            self?.startBackend()
        }
    }

    @objc private func reloadTown() {
        if origin == nil { retryStartup() } else { webView.reload() }
    }

    @objc private func returnToTown() {
        showWindow()
        if let url = origin { webView.load(URLRequest(url: url.appendingFragment("town"))) }
    }

    @objc private func showSettings() {
        showWindow()
        if let url = origin { webView.load(URLRequest(url: url.appendingFragment("settings"))) }
    }

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.messageText = "星栖小镇 \(version)"
        alert.informativeText = "属于你的像素智能体小镇。\n\n居民资料、记忆和聊天记录保存在这台 Mac 上。AI 对话使用你配置的模型服务。"
        alert.addButton(withTitle: "好")
        alert.runModal()
    }

    @objc private func showUpdateHelp() {
        let alert = NSAlert()
        alert.messageText = "安装与更新 · 当前版本 \(version)"
        alert.informativeText = "下载新版安装器并再次运行，即可更新星栖小镇。更新前请先退出应用。\n\n角色、记忆和聊天记录保存在独立的数据文件夹中，更新应用会保留这些内容。\n\n当前版本没有在线自动更新服务。"
        alert.addButton(withTitle: "知道了")
        alert.addButton(withTitle: "打开数据文件夹")
        if alert.runModal() == .alertSecondButtonReturn { openDataDirectory() }
    }

    @objc private func openDataDirectory() {
        try? fileManager.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(dataDirectory)
    }

    @objc private func selectTeamDirectory() {
        let panel = NSOpenPanel()
        panel.title = "连接智能体团队"
        panel.message = "选择包含 config.json 和 state.json 的协调官目录。小镇只读取团队进度，不会自动派发工作。"
        panel.prompt = "连接目录"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: mainWindow) { [weak self] response in
            guard let self = self, response == .OK, let selected = panel.url else { return }
            let configURL = selected.appendingPathComponent("config.json")
            let stateURL = selected.appendingPathComponent("state.json")
            guard let configData = try? Data(contentsOf: configURL),
                  let config = try? JSONSerialization.jsonObject(with: configData) as? [String: Any],
                  let stateData = try? Data(contentsOf: stateURL),
                  (try? JSONSerialization.jsonObject(with: stateData) as? [String: Any]) != nil else {
                self.showNotice("这个目录还不能连接", "请选取同时包含有效 config.json 和 state.json 的协调官目录。")
                return
            }
            do {
                let settingsURL = self.dataDirectory.appendingPathComponent("settings.json")
                var settings: [String: Any] = [:]
                if self.fileManager.fileExists(atPath: settingsURL.path) {
                    let bytes = try Data(contentsOf: settingsURL)
                    guard let saved = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                        throw NSError(domain: self.bundleID, code: 3, userInfo: [NSLocalizedDescriptionKey: "现有设置无法读取，为保留原设置，本次没有更改。"])
                    }
                    settings = saved
                }
                settings["source_dir"] = selected.path
                settings.removeValue(forKey: "report_root")
                if let integrations = config["integrations"] as? [String: Any],
                   let scout = integrations["collect_skill"] as? [String: Any],
                   let workdir = scout["workdir"] as? String {
                    let reports = URL(fileURLWithPath: workdir, isDirectory: true).appendingPathComponent("daily", isDirectory: true)
                    var isDirectory: ObjCBool = false
                    if self.fileManager.fileExists(atPath: reports.path, isDirectory: &isDirectory), isDirectory.boolValue {
                        settings["report_root"] = reports.path
                    }
                }
                let bytes = try JSONSerialization.data(withJSONObject: settings, options: [.prettyPrinted, .sortedKeys])
                try bytes.write(to: settingsURL, options: .atomic)
                self.showNotice("团队目录已保存", "退出并重新打开星栖小镇后生效。原有角色和聊天记录会保留。")
            } catch {
                self.showNotice("没有保存成功", error.localizedDescription)
            }
        }
    }

    private func showNotice(_ title: String, _ detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "好")
        alert.beginSheetModal(for: mainWindow)
    }

    private func isOwnURL(_ url: URL) -> Bool {
        guard let origin = origin else { return false }
        return url.scheme?.lowercased() == "http" && url.host == "127.0.0.1" && url.port == origin.port
            && url.user == nil && url.password == nil
    }

    private func openExternal(_ url: URL) {
        guard let scheme = url.scheme?.lowercased(), ["https", "http"].contains(scheme),
              url.host != nil, url.user == nil, url.password == nil else { return }
        NSWorkspace.shared.open(url)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if isOwnURL(url) {
            if navigationAction.targetFrame == nil {
                openReport(url)
                decisionHandler(.cancel)
            } else {
                decisionHandler(.allow)
            }
        } else {
            if navigationAction.targetFrame?.isMainFrame != false { openExternal(url) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = navigationResponse.response.url, isOwnURL(url) else { decisionHandler(.cancel); return }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if isOwnURL(url) { openReport(url) } else { openExternal(url) }
        }
        return nil
    }

    private func openReport(_ url: URL) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 800),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "小镇资料 · 星栖小镇"
        window.minSize = NSSize(width: 700, height: 500)
        window.isReleasedWhenClosed = false
        window.delegate = self
        let report = WKWebView()
        report.navigationDelegate = self
        report.uiDelegate = self
        window.contentView = report
        reportWindows.append(window)
        window.center()
        window.makeKeyAndOrderFront(nil)
        report.load(URLRequest(url: url))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === self.webView {
            spinner.stopAnimation(nil)
            statusView.isHidden = true
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showNavigationError(webView, error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showNavigationError(webView, error)
    }

    private func showNavigationError(_ view: WKWebView, _ error: Error) {
        guard view === webView, (error as NSError).code != NSURLErrorCancelled, !quitting else { return }
        showStartupError("页面暂时无法加载。请点击“重新打开小镇”恢复连接。居民和记忆已经保存在本机。")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === mainWindow {
            sender.orderOut(nil)
            return false
        }
        return true
    }

    func windowWillClose(_ notification: Notification) {
        if let window = notification.object as? NSWindow { reportWindows.removeAll { $0 === window } }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        return true
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        if let mainWindow = mainWindow, !mainWindow.isVisible { mainWindow.makeKeyAndOrderFront(nil) }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateNow }
        quitting = true
        attemptID = UUID()
        guard backend?.isRunning == true else { stopBackend {}; return .terminateNow }
        stopBackend { NSApp.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        readyTimer?.invalidate()
        if let process = backend, process.isRunning { process.terminate() }
        removeReadyDirectory()
    }
}

private extension URL {
    func appendingFragment(_ fragment: String) -> URL {
        var parts = URLComponents(url: self, resolvingAgainstBaseURL: false)!
        parts.fragment = fragment
        return parts.url!
    }
}

let application = NSApplication.shared
let delegate = TownAppDelegate()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()
