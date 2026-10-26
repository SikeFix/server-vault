// Server Vault 菜单栏应用:顶部状态栏图标 → 弹出管理界面;自动启动/守护本地服务。
import AppKit
import ServiceManagement
import WebKit

let info = Bundle.main.infoDictionary ?? [:]
let homeDir = (info["ServerVaultHome"] as? String) ?? FileManager.default.currentDirectoryPath
let port = (info["ServerVaultPort"] as? String) ?? "8780"
let baseURL = URL(string: "http://127.0.0.1:\(port)/")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    var webView: WKWebView!
    var config = WKWebViewConfiguration()
    var server: Process?
    var windows: [NSWindow] = []
    var mainWindow: NSWindow?

    func applicationDidFinishLaunching(_ note: Notification) {
        setupEditMenu()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            let img = NSImage(systemSymbolName: "lock.shield.fill", accessibilityDescription: "Server Vault")
            img?.isTemplate = true
            button.image = img
            button.action = #selector(clicked(_:))
            button.target = self
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
        config.websiteDataStore = .default()
        config.applicationNameForUserAgent = "ServerVaultApp/1.5"
        webView = makeWebView(frame: NSRect(x: 0, y: 0, width: 1360, height: 860))
        showLoading("正在启动 Server Vault…")
        ensureServer()
    }

    // 隐藏的编辑菜单:让 ⌘C / ⌘V / ⌘A 在网页输入框中可用
    func setupEditMenu() {
        let main = NSMenu()
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        edit.addItem(withTitle: "刷新", action: #selector(reloadPage), keyEquivalent: "r")
        editItem.submenu = edit
        NSApp.mainMenu = main
    }

    func makeWebView(frame: NSRect) -> WKWebView {
        let wv = WKWebView(frame: frame, configuration: config)
        wv.uiDelegate = self
        wv.setValue(false, forKey: "drawsBackground")
        return wv
    }

    func showLoading(_ text: String) {
        webView.loadHTMLString("""
        <html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#0b1020;color:#aab3cc;
        font:15px -apple-system,'PingFang SC'"><div style="text-align:center"><div style="font-size:38px">🔐</div>
        <p>\(text)</p></div></body></html>
        """, baseURL: nil)
    }

    // ---------- 服务进程 ----------
    func serverAlive(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/state"))
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            done((resp as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }

    func ensureServer() {
        serverAlive { alive in
            DispatchQueue.main.async {
                if !alive { self.startServer() }
                self.waitReady(tries: 60)
            }
        }
    }

    func startServer() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "\(homeDir)/.venv/bin/python")
        p.arguments = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port]
        p.currentDirectoryURL = URL(fileURLWithPath: homeDir)
        var env = ProcessInfo.processInfo.environment
        env["SV_PORT"] = port
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
        p.environment = env
        let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/ServerVault.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let fh = try? FileHandle(forWritingTo: logURL) {
            fh.seekToEndOfFile()
            p.standardOutput = fh
            p.standardError = fh
        }
        do {
            try p.run()
            server = p
        } catch {
            showLoading("启动失败:\(error.localizedDescription)<br>请确认 \(homeDir)/.venv 存在(先运行一次 run.sh)")
        }
    }

    func waitReady(tries: Int) {
        serverAlive { alive in
            DispatchQueue.main.async {
                if alive {
                    self.webView.load(URLRequest(url: baseURL))
                } else if tries > 0 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitReady(tries: tries - 1) }
                } else {
                    self.showLoading("服务未能启动,日志:~/Library/Logs/ServerVault.log")
                }
            }
        }
    }

    // ---------- 交互 ----------
    @objc func clicked(_ sender: NSStatusBarButton) {
        if NSApp.currentEvent?.type == .rightMouseUp {
            let menu = buildMenu()
            statusItem.menu = menu
            statusItem.button?.performClick(nil)
            statusItem.menu = nil
            return
        }
        toggleWindow()
    }

    // 点击菜单栏图标:弹出 / 收起居中的主窗口(网页状态保留,不会重新加载)
    func toggleWindow() {
        if let w = mainWindow, w.isVisible, w.isKeyWindow, NSApp.isActive {
            w.orderOut(nil)
            return
        }
        showMainWindow()
    }

    func showMainWindow() {
        if mainWindow == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 860),
                             styleMask: [.titled, .closable, .resizable, .miniaturizable, .fullSizeContentView],
                             backing: .buffered, defer: false)
            w.title = "Server Vault"
            w.titlebarAppearsTransparent = true
            w.titleVisibility = .hidden
            w.contentView = webView
            w.isReleasedWhenClosed = false
            w.minSize = NSSize(width: 900, height: 600)
            w.setFrameAutosaveName("ServerVaultMain")
            if !w.setFrameUsingName("ServerVaultMain") { w.center() }
            mainWindow = w
        }
        NSApp.activate(ignoringOtherApps: true)
        mainWindow?.makeKeyAndOrderFront(nil)
    }

    func buildMenu() -> NSMenu {
        let m = NSMenu()
        m.addItem(withTitle: "打开 Server Vault", action: #selector(openWindow), keyEquivalent: "")
        m.addItem(withTitle: "在浏览器中打开", action: #selector(openBrowser), keyEquivalent: "")
        m.addItem(.separator())
        let login = NSMenuItem(title: "开机自动启动", action: #selector(toggleLogin), keyEquivalent: "")
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        m.addItem(login)
        m.addItem(withTitle: "重启服务", action: #selector(restartServer), keyEquivalent: "")
        m.addItem(withTitle: "查看日志", action: #selector(openLog), keyEquivalent: "")
        m.addItem(.separator())
        let ver = NSMenuItem(title: "服务地址 127.0.0.1:\(port)", action: nil, keyEquivalent: "")
        ver.isEnabled = false
        m.addItem(ver)
        m.addItem(withTitle: "退出 Server Vault", action: #selector(quit), keyEquivalent: "q")
        for item in m.items { item.target = self }
        return m
    }

    @objc func reloadPage() { (NSApp.keyWindow?.contentView as? WKWebView ?? webView).reload() }
    @objc func openBrowser() { NSWorkspace.shared.open(baseURL) }
    @objc func openLog() {
        NSWorkspace.shared.open(FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/ServerVault.log"))
    }

    @objc func openWindow() { showMainWindow() }

    func newWindow(title: String, view: NSView, size: NSSize) -> NSWindow {
        let w = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                         styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        w.title = title
        w.contentView = view
        w.isReleasedWhenClosed = false
        w.center()
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        windows.append(w)
        return w
    }

    @objc func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
            else { try SMAppService.mainApp.register() }
        } catch {
            let a = NSAlert()
            a.messageText = "设置开机启动失败"
            a.informativeText = error.localizedDescription
            a.runModal()
        }
    }

    @objc func restartServer() {
        server?.terminate()
        server?.waitUntilExit()
        server = nil
        showLoading("正在重启服务…")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.ensureServer() }
    }

    @objc func quit() {
        server?.terminate()
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ note: Notification) { server?.terminate() }

    // 再次打开应用(如在启动台/访达双击)时也弹出主窗口
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    // window.open / target=_blank:在应用内新窗口打开(共享登录状态,终端、宝塔一键登录可用)
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        let wv = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 700), configuration: configuration)
        wv.uiDelegate = self
        _ = newWindow(title: "Server Vault", view: wv, size: NSSize(width: 1100, height: 700))
        return wv
    }

    func webViewDidClose(_ webView: WKWebView) { webView.window?.close() }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.runModal()
        completionHandler()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
// 被 kill / 注销时一并停止由本应用启动的服务进程
signal(SIGTERM, SIG_IGN)
let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigterm.setEventHandler { delegate.server?.terminate(); exit(0) }
sigterm.resume()
app.run()
