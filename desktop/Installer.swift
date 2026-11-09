import AppKit
import Foundation
import Darwin

private let townBundleID = "cn.starhaven.town"
private let townAppName = "星栖小镇.app"

private struct InstallFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private struct InstallOptions {
    let installRoot: URL
    let desktopDirectory: URL
    let backupDirectory: URL
    let shouldLaunch: Bool

    static func standard(launch: Bool = true) -> InstallOptions {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return InstallOptions(
            installRoot: home.appendingPathComponent("Applications", isDirectory: true),
            desktopDirectory: home.appendingPathComponent("Desktop", isDirectory: true),
            backupDirectory: home.appendingPathComponent("Library/Application Support/StarhavenTown/App Backups", isDirectory: true),
            shouldLaunch: launch)
    }
}

private struct InstallResult {
    let target: URL
    let desktopLink: URL
    let backup: URL?
    let launchWarning: String?
}

private func onMain<T>(_ action: () -> T) -> T {
    if Thread.isMainThread { return action() }
    return DispatchQueue.main.sync(execute: action)
}

private final class TownInstaller {
    private let manager = FileManager.default
    private let payload: URL

    init() throws {
        guard let resources = Bundle.main.resourceURL else {
            throw InstallFailure(message: "安装器资源不完整，请重新下载。")
        }
        payload = resources.appendingPathComponent("Payload", isDirectory: true).appendingPathComponent(townAppName, isDirectory: true)
    }

    private func info(_ application: URL) throws -> [String: Any] {
        let file = application.appendingPathComponent("Contents/Info.plist")
        guard let bytes = try? Data(contentsOf: file),
              let value = try? PropertyListSerialization.propertyList(from: bytes, format: nil) as? [String: Any] else {
            throw InstallFailure(message: "无法读取应用信息：\(application.lastPathComponent)。请重新下载完整安装器。")
        }
        return value
    }

    var payloadVersion: String {
        (try? info(payload)["CFBundleShortVersionString"] as? String) ?? "未知版本"
    }

    func hasExistingTown(_ options: InstallOptions) -> Bool {
        let target = options.installRoot.appendingPathComponent(townAppName, isDirectory: true)
        return (try? info(target)["CFBundleIdentifier"] as? String) == townBundleID
    }

    private func entryExists(_ url: URL) -> Bool {
        (try? manager.attributesOfItem(atPath: url.path)) != nil
    }

    private func isSymlink(_ url: URL) -> Bool {
        (try? manager.attributesOfItem(atPath: url.path)[.type] as? FileAttributeType) == .typeSymbolicLink
    }

    private func linkPointsToTarget(_ link: URL, target: URL) -> Bool {
        guard isSymlink(link), let destination = try? manager.destinationOfSymbolicLink(atPath: link.path) else { return false }
        let destinationURL = destination.hasPrefix("/")
            ? URL(fileURLWithPath: destination)
            : link.deletingLastPathComponent().appendingPathComponent(destination)
        return destinationURL.standardizedFileURL.path == target.standardizedFileURL.path
    }

    private func validateDesktop(_ link: URL, target: URL) throws {
        if entryExists(link) && !linkPointsToTarget(link, target: target) {
            throw InstallFailure(message: "桌面已有同名内容“星栖小镇.app”。为保留它，本次没有覆盖。请先将已有同名内容改名或移到其他位置，再安装。")
        }
    }

    private func verifySignature(_ application: URL) throws {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
        process.arguments = ["--verify", "--deep", "--strict", application.path]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = output
        process.standardError = output
        try process.run()
        _ = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw InstallFailure(message: "应用完整性验证没有通过。请重新下载完整安装包后再试，本次没有更改已安装的应用。")
        }
    }

    private func requestExistingAppsToQuit() throws {
        let apps = onMain {
            NSRunningApplication.runningApplications(withBundleIdentifier: townBundleID).filter { !$0.isTerminated }
        }
        guard !apps.isEmpty else { return }
        let requested = onMain { apps.allSatisfy { $0.terminate() || $0.isTerminated } }
        guard requested else {
            throw InstallFailure(message: "星栖小镇尚未退出。请在小镇菜单中选择“退出星栖小镇”，然后重新安装。")
        }
        let deadline = Date().addingTimeInterval(25)
        while Date() < deadline {
            if onMain({ apps.allSatisfy { $0.isTerminated } }) { return }
            Thread.sleep(forTimeInterval: 0.1)
        }
        throw InstallFailure(message: "星栖小镇仍在停止对话并保存进度。安装器没有强制关闭它，请等待小镇退出后重新安装。")
    }

    func install(_ options: InstallOptions, progress: @escaping (String) -> Void) throws -> InstallResult {
        let target = options.installRoot.appendingPathComponent(townAppName, isDirectory: true)
        let desktopLink = options.desktopDirectory.appendingPathComponent(townAppName)
        guard target.standardizedFileURL != desktopLink.standardizedFileURL else {
            throw InstallFailure(message: "应用安装目录和桌面目录需要是两个不同的文件夹。")
        }
        progress("正在验证安装包…")
        guard try info(payload)["CFBundleIdentifier"] as? String == townBundleID else {
            throw InstallFailure(message: "安装包不是星栖小镇，请重新下载正确的安装器。")
        }
        try verifySignature(payload)
        try validateDesktop(desktopLink, target: target)
        try manager.createDirectory(at: options.installRoot, withIntermediateDirectories: true)
        let lockURL = options.installRoot.appendingPathComponent(".starhaven-install.lock")
        let descriptor = open(lockURL.path, O_CREAT | O_RDWR | O_NOFOLLOW, mode_t(0o600))
        guard descriptor >= 0 else { throw InstallFailure(message: "无法写入应用安装目录，请检查文件夹权限。") }
        defer { flock(descriptor, LOCK_UN); close(descriptor) }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            throw InstallFailure(message: "另一个星栖小镇安装器正在工作。请等待它完成后再试。")
        }

        let hadExisting = entryExists(target)
        var oldVersion = "unknown"
        if hadExisting {
            guard !isSymlink(target), try info(target)["CFBundleIdentifier"] as? String == townBundleID else {
                throw InstallFailure(message: "安装位置已有其他同名应用或链接，为保护它，本次没有覆盖。请检查 \(target.path)。")
            }
            oldVersion = (try info(target)["CFBundleShortVersionString"] as? String) ?? "unknown"
        }
        progress("正在准备应用…")
        let stage = options.installRoot.appendingPathComponent(".Starhaven-install-\(UUID().uuidString).app", isDirectory: true)
        defer { if entryExists(stage) { try? manager.removeItem(at: stage) } }
        try manager.copyItem(at: payload, to: stage)
        try verifySignature(stage)
        try validateDesktop(desktopLink, target: target)
        progress(hadExisting ? "正在等待对话停止、进度保存并退出小镇…" : "正在完成安装…")
        try requestExistingAppsToQuit()

        var backup: URL?
        var installedNew = false
        var createdLink = false
        do {
            if hadExisting {
                guard !isSymlink(target), try info(target)["CFBundleIdentifier"] as? String == townBundleID else {
                    throw InstallFailure(message: "安装位置的原应用发生了变化，本次已停止，以保留现有内容。")
                }
                try manager.createDirectory(at: options.backupDirectory, withIntermediateDirectories: true)
                let formatter = DateFormatter()
                formatter.locale = Locale(identifier: "en_US_POSIX")
                formatter.timeZone = TimeZone(secondsFromGMT: 0)
                formatter.dateFormat = "yyyyMMdd-HHmmss-SSS"
                let safeVersion = oldVersion.replacingOccurrences(of: "[^A-Za-z0-9._-]", with: "_", options: .regularExpression)
                let name = "\(formatter.string(from: Date()))-v\(safeVersion)-\(UUID().uuidString.prefix(6)).app"
                let destination = options.backupDirectory.appendingPathComponent(name, isDirectory: true)
                try manager.moveItem(at: target, to: destination)
                backup = destination
            } else if entryExists(target) {
                throw InstallFailure(message: "安装位置刚刚出现了同名内容，本次已停止，请检查后重试。")
            }
            try manager.moveItem(at: stage, to: target)
            installedNew = true
            try manager.createDirectory(at: options.desktopDirectory, withIntermediateDirectories: true)
            try validateDesktop(desktopLink, target: target)
            if !entryExists(desktopLink) {
                try manager.createSymbolicLink(atPath: desktopLink.path, withDestinationPath: target.path)
                createdLink = true
            }
        } catch {
            var rollbackIssue: String?
            if createdLink && linkPointsToTarget(desktopLink, target: target) { try? manager.removeItem(at: desktopLink) }
            if installedNew {
                do { try manager.moveItem(at: target, to: stage) }
                catch { rollbackIssue = "无法移走未完成的新应用：\(error.localizedDescription)" }
            }
            if let saved = backup, !entryExists(target) {
                do {
                    // Keep the backup as requested, including when a later installation step fails.
                    try manager.copyItem(at: saved, to: target)
                } catch {
                    rollbackIssue = "旧版本仍保留在 \(saved.path)，但自动恢复没有成功：\(error.localizedDescription)"
                }
            }
            let suffix = rollbackIssue.map { "\n\($0)" } ?? "\n原有居民、记忆和聊天记录没有更改。"
            throw InstallFailure(message: "安装没有完成：\(error.localizedDescription)\(suffix)")
        }

        var launchWarning: String?
        if options.shouldLaunch {
            progress("安装完成，正在打开小镇…")
            if !onMain({ NSWorkspace.shared.open(target) }) {
                launchWarning = "应用已安装完成，但没有自动打开。请双击桌面的星栖小镇图标。"
            }
        }
        return InstallResult(target: target, desktopLink: desktopLink, backup: backup, launchWarning: launchWarning)
    }
}

private final class InstallerAppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var detail: NSTextField!
    private var button: NSButton!
    private var spinner: NSProgressIndicator!
    private var installer: TownInstaller?
    private var installing = false
    private var finished = false
    private let options = InstallOptions.standard()

    func applicationDidFinishLaunching(_ notification: Notification) {
        installer = try? TownInstaller()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 420),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "星栖小镇安装器"
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 18
        stack.translatesAutoresizingMaskIntoConstraints = false
        let icon = NSImageView(image: NSImage(named: NSImage.applicationIconName) ?? NSImage())
        icon.translatesAutoresizingMaskIntoConstraints = false
        icon.widthAnchor.constraint(equalToConstant: 76).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 76).isActive = true
        let title = NSTextField(labelWithString: "把星栖小镇带到桌面")
        title.font = .systemFont(ofSize: 24, weight: .semibold)
        let version = NSTextField(labelWithString: "版本 \(installer?.payloadVersion ?? "未知") · macOS")
        version.textColor = .secondaryLabelColor
        detail = NSTextField(wrappingLabelWithString: "一键安装，双击桌面图标就能进入小镇。\n更新应用会保留你的居民、记忆和聊天记录。")
        detail.alignment = .center
        detail.font = .systemFont(ofSize: 14)
        detail.translatesAutoresizingMaskIntoConstraints = false
        detail.widthAnchor.constraint(equalToConstant: 430).isActive = true
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.isIndeterminate = true
        spinner.isHidden = true
        button = NSButton(title: (installer?.hasExistingTown(options) ?? false) ? "更新并打开" : "安装并打开",
                          target: self, action: #selector(beginInstall))
        button.bezelStyle = .rounded
        button.keyEquivalent = "\r"
        button.controlSize = .large
        for view in [icon, title, version, detail!, spinner!, button!] as [NSView] { stack.addArrangedSubview(view) }
        let content = window.contentView!
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: content.centerYAnchor)
        ])
        let main = NSMenu()
        let item = NSMenuItem()
        let menu = NSMenu(title: "安装器")
        menu.addItem(NSMenuItem(title: "退出安装器", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        item.submenu = menu
        main.addItem(item)
        NSApp.mainMenu = main
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func beginInstall() {
        if finished { NSApp.terminate(nil); return }
        guard !installing else { return }
        guard let installer = installer else { detail.stringValue = "安装器资源不完整，请重新下载。"; return }
        installing = true
        button.isEnabled = false
        spinner.isHidden = false
        spinner.startAnimation(nil)
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let result = try installer.install(self.options) { message in
                    DispatchQueue.main.async { self.detail.stringValue = message }
                }
                DispatchQueue.main.async {
                    self.installing = false
                    self.finished = true
                    self.spinner.stopAnimation(nil)
                    self.spinner.isHidden = true
                    self.button.isEnabled = true
                    self.button.title = "完成"
                    self.detail.stringValue = result.launchWarning ?? "安装完成。以后双击桌面的星栖小镇图标即可打开。"
                }
            } catch {
                DispatchQueue.main.async {
                    self.installing = false
                    self.spinner.stopAnimation(nil)
                    self.spinner.isHidden = true
                    self.button.isEnabled = true
                    let alert = NSAlert()
                    alert.messageText = "安装暂未完成"
                    alert.informativeText = error.localizedDescription
                    alert.addButton(withTitle: "好")
                    alert.beginSheetModal(for: self.window)
                    self.detail.stringValue = "处理提示中的问题后，可以再次安装。"
                }
            }
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { !installing }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        installing ? .terminateCancel : .terminateNow
    }
}

private func parseCommandLine(_ arguments: [String]) throws -> InstallOptions {
    var options = InstallOptions.standard()
    var installRoot: URL?
    var desktop: URL?
    var launch = true
    var index = 0
    while index < arguments.count {
        let argument = arguments[index]
        switch argument {
        case "--install": break
        case "--no-launch": launch = false
        case "--install-root", "--desktop-dir":
            index += 1
            guard index < arguments.count, arguments[index].hasPrefix("/") else {
                throw InstallFailure(message: "\(argument) 需要绝对路径。")
            }
            let value = URL(fileURLWithPath: arguments[index], isDirectory: true).standardizedFileURL
            if argument == "--install-root" { installRoot = value } else { desktop = value }
        default: throw InstallFailure(message: "未知参数：\(argument)")
        }
        index += 1
    }
    options = InstallOptions(installRoot: installRoot ?? options.installRoot,
                             desktopDirectory: desktop ?? options.desktopDirectory,
                             backupDirectory: installRoot?.appendingPathComponent(".starhaven-backups", isDirectory: true) ?? options.backupDirectory,
                             shouldLaunch: launch)
    return options
}

let arguments = Array(CommandLine.arguments.dropFirst())
let application = NSApplication.shared
if arguments.contains("--install") {
    application.setActivationPolicy(.prohibited)
    do {
        let options = try parseCommandLine(arguments)
        let installer = try TownInstaller()
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                let result = try installer.install(options) { _ in }
                print("安装成功：\(result.target.path)")
                print("桌面图标：\(result.desktopLink.path)")
                if let backup = result.backup { print("旧版本备份：\(backup.path)") }
                if let warning = result.launchWarning { print(warning) }
                fflush(stdout)
                DispatchQueue.main.async { exit(0) }
            } catch {
                FileHandle.standardError.write(Data("安装失败：\(error.localizedDescription)\n".utf8))
                DispatchQueue.main.async { exit(1) }
            }
        }
        application.run()
    } catch {
        FileHandle.standardError.write(Data("参数或安装器错误：\(error.localizedDescription)\n".utf8))
        exit(2)
    }
} else {
    guard arguments.isEmpty else {
        FileHandle.standardError.write(Data("命令行安装请使用 --install，可追加 --no-launch。\n".utf8))
        exit(2)
    }
    application.setActivationPolicy(.regular)
    let delegate = InstallerAppDelegate()
    application.delegate = delegate
    withExtendedLifetime(delegate) { application.run() }
}
