// C-3PO.app: the native window for c3po.
//
// A thin AppKit shell around the existing Python server. It starts the server
// (`scripts/c3po-open.sh --serve`), shows startup progress, then hosts the
// server's page in a WKWebView. The window toolbar replaces the page's button
// row: the page reports its state through the "c3po" message handler, and the
// toolbar presses the page's own buttons via `window.c3poNative` (panel.js), so
// the recording logic stays in WhisperLiveKit.
//
// Built by scripts/make-overlay-app.sh, which bakes the repo path and port into
// Info.plist (C3PORepo, C3POPort).

import Cocoa
import UniformTypeIdentifiers
import WebKit

private let info = Bundle.main.infoDictionary ?? [:]
private let repo = info["C3PORepo"] as? String ?? ""
private let port = Int(info["C3POPort"] as? String ?? "") ?? 8989
private let pageURL = URL(string: "http://localhost:\(port)/")!
// Probe by IP: the server binds 127.0.0.1 only (C3PO_HOST in config.py).
private let probeURL = URL(string: "http://127.0.0.1:\(port)/")!
private let transcriptURL = URL(string: "http://127.0.0.1:\(port)/transcript")!
private let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".c3po-server.log")
// Same as READY_TIMEOUT in c3po-open.sh: the Whisper model loads on every start (~80s).
private let readyTimeout: TimeInterval = 180
// Same as GRACE in c3po-quit.sh. A clean shutdown takes ~1s; this is the ceiling.
private let quitGrace: TimeInterval = 30

private extension NSToolbarItem.Identifier {
    static let record = Self("record")
    static let stop = Self("stop")
    static let waveform = Self("waveform")
    static let save = Self("save")
    static let toggles = Self("toggles")
}

/// Live waveform next to Record/Stop, fed by the page's analyser (panel.js
/// posts ~20 frames/s of samples in -1...1). A flat line when not recording.
final class WaveformView: NSView {
    var samples: [Double] = [] {
        didSet { needsDisplay = true }
    }

    override var intrinsicContentSize: NSSize { NSSize(width: 120, height: 22) }

    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath()
        path.lineWidth = 1.2
        path.lineJoinStyle = .round
        let mid = bounds.midY
        if samples.count < 2 {
            path.move(to: NSPoint(x: bounds.minX, y: mid))
            path.line(to: NSPoint(x: bounds.maxX, y: mid))
            NSColor.tertiaryLabelColor.setStroke()
        } else {
            let step = bounds.width / CGFloat(samples.count - 1)
            for (i, value) in samples.enumerated() {
                let point = NSPoint(x: bounds.minX + CGFloat(i) * step,
                                    y: mid + CGFloat(max(-1, min(1, value))) * bounds.height / 2)
                if i == 0 { path.move(to: point) } else { path.line(to: point) }
            }
            NSColor.labelColor.setStroke()
        }
        path.stroke()
    }
}

/// The segments of the toolbar's right-hand group, with the page action each one runs.
private enum Toggle: Int, CaseIterable {
    case settings, subtitles, suggestions

    var symbol: String { ["gearshape", "captions.bubble", "sidebar.right"][rawValue] }
    var label: String { ["Settings", "Subtitles", "Suggestions"][rawValue] }
    var action: String { ["toggleSettings()", "toggleOverlay()", "togglePanel()"][rawValue] }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSToolbarDelegate,
                         NSToolbarItemValidation, NSMenuItemValidation, WKUIDelegate,
                         WKNavigationDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let toggles = NSSegmentedControl()
    private let waveform = WaveformView()
    // Proxies are bypassed for the same reason c3po-open.sh uses --noproxy:
    // with a proxy configured, requests to localhost fail and the server looks dead.
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]
        return URLSession(configuration: config)
    }()

    /// The server we started. nil when we attached to one that was already running,
    /// whose lifetime is not ours to manage.
    private var server: Process?
    /// The server answered on the port. Before that it is still loading the model.
    private var ready = false
    private var quitting = false
    private var quitConfirmed = false
    private var pageLoaded = false
    private var recordingActivity: NSObjectProtocol?

    // Page state, as last reported by panel.js.
    private var recording = false
    private var pending = false
    private var busy = false
    private var toggleState: [Toggle: Bool] = [:]

    // MARK: - Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        showLoading()
        guard FileManager.default.isExecutableFile(atPath: repo + "/scripts/c3po-open.sh") else {
            showError("scripts/c3po-open.sh not found in \"\(repo)\". Did the repo move? "
                      + "Rebuild the app: bash scripts/make-overlay-app.sh", includeLog: false)
            return
        }
        connect()
    }

    /// Attach to a running server, or start one.
    private func connect() {
        probe { [weak self] up in
            guard let self else { return }
            if up {
                self.ready = true
                self.showPage()
            } else {
                self.startServer()
            }
        }
    }

    private func startServer() {
        ready = false
        FileManager.default.createFile(atPath: logURL.path, contents: nil)  // truncate
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [repo + "/scripts/c3po-open.sh", "--serve"]
        var env = ProcessInfo.processInfo.environment
        // A GUI app gets launchd's minimal PATH and C locale.
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        env["LANG"] = "en_US.UTF-8"
        env["LC_ALL"] = "en_US.UTF-8"
        // Lets the server shut itself down if this app dies without quitting.
        env["C3PO_PARENT_PID"] = String(getpid())
        // The port this app probes; overrides any C3PO_PORT in .env.
        env["C3PO_PORT"] = String(port)
        process.environment = env
        process.currentDirectoryURL = URL(fileURLWithPath: repo)
        process.standardInput = FileHandle.nullDevice
        if let log = try? FileHandle(forWritingTo: logURL) {
            process.standardOutput = log
            process.standardError = log
        }
        process.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.serverExited(proc) }
        }
        do {
            try process.run()
        } catch {
            showError("Could not start the server: \(error.localizedDescription)", includeLog: false)
            return
        }
        server = process
        waitForServer(since: Date())
    }

    private func waitForServer(since start: Date) {
        probe { [weak self] up in
            guard let self, !self.quitting, let server = self.server, server.isRunning else {
                return  // serverExited() reports an early exit
            }
            if up {
                self.ready = true
                self.showPage()
                return
            }
            let waited = Date().timeIntervalSince(start)
            if waited > readyTimeout {
                // Detach first so serverExited() ignores this exit: it is ours,
                // not a clean stop to quit on. SIGKILL because uvicorn only acts
                // on SIGTERM once startup (the model load) has finished.
                self.server = nil
                kill(server.processIdentifier, SIGKILL)
                self.showError("The server did not become ready in \(Int(readyTimeout))s.")
                return
            }
            self.window.subtitle = "Loading speech model… \(Int(waited))s"
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.waitForServer(since: start) }
        }
    }

    private func probe(_ done: @escaping (Bool) -> Void) {
        var request = URLRequest(url: probeURL)
        request.timeoutInterval = 3
        session.dataTask(with: request) { _, response, _ in
            let up = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(up) }
        }.resume()
    }

    private func serverExited(_ process: Process) {
        guard process === server else { return }
        server = nil
        resetPageState()  // also keeps a stale "recording" from prompting on quit
        if quitting {
            NSApp.reply(toApplicationShouldTerminate: true)
            return
        }
        // uvicorn re-raises the SIGTERM it shut down on, so a clean stop
        // (C-3PO Quit.app, c3po-quit.sh) ends by signal 15, not exit code 0.
        let status = process.terminationStatus
        let clean = process.terminationReason == .uncaughtSignal ? status == SIGTERM : status == 0
        if clean {
            NSApp.terminate(nil)
        } else {
            let how = process.terminationReason == .uncaughtSignal ? "signal \(status)" : "exit code \(status)"
            showError("The c3po server stopped (\(how)).")
        }
    }

    // MARK: - Quit

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard confirmQuitWhileRecording() else { return .terminateCancel }
        guard let server, server.isRunning else { return .terminateNow }
        // Hide the window now so quitting feels immediate; serverExited() replies.
        quitting = true
        window.orderOut(nil)
        guard ready else {
            // Still loading the model: uvicorn would only act on SIGTERM after
            // startup, and there is nothing to shut down cleanly yet.
            kill(server.processIdentifier, SIGKILL)
            return .terminateLater
        }
        // Let the server's shutdown handler run (it also stops the overlay).
        server.terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + quitGrace) {
            if server.isRunning { kill(server.processIdentifier, SIGKILL) }
        }
        return .terminateLater
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { confirmQuitWhileRecording() }

    /// Ask before stopping a live transcription: getting back costs the model
    /// reload. Same safeguard as the confirm on the page's power button.
    private func confirmQuitWhileRecording() -> Bool {
        guard recording, !quitConfirmed else { return true }
        let alert = NSAlert()
        alert.messageText = "Quit C-3PO?"
        alert.informativeText = "Transcription is running. Quitting stops the server and the subtitles."
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Cancel")
        quitConfirmed = alert.runModal() == .alertFirstButtonReturn
        return quitConfirmed
    }

    // MARK: - Window and content

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "C-3PO"
        window.subtitle = "Starting…"
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.minSize = NSSize(width: 760, height: 480)
        window.center()
        window.setFrameAutosaveName("C3POMainWindow")

        let toolbar = NSToolbar(identifier: "C3POToolbar")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        window.toolbar = toolbar
        window.toolbarStyle = .unified

        toggles.segmentCount = Toggle.allCases.count
        toggles.trackingMode = .selectAny
        for toggle in Toggle.allCases {
            toggles.setImage(NSImage(systemSymbolName: toggle.symbol, accessibilityDescription: toggle.label),
                             forSegment: toggle.rawValue)
            toggles.setToolTip(toggle.label, forSegment: toggle.rawValue)
        }
        toggles.target = self
        toggles.action = #selector(toggleClicked(_:))
        toggles.isEnabled = false

        let config = WKWebViewConfiguration()
        let content = config.userContentController
        // Tells panel.css to hide the in-page button row (the toolbar replaces it).
        content.addUserScript(WKUserScript(
            source: "document.documentElement.classList.add('c3po-native');",
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        content.add(self, name: "c3po")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.isInspectable = true  // Safari > Develop, since there is no console here

        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    private func showLoading() {
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.startAnimation(nil)
        let label = NSTextField(labelWithString: "Starting c3po. The speech model takes about 80 seconds to load.")
        label.textColor = .secondaryLabelColor
        window.contentView = centered([spinner, label])
    }

    private func showPage() {
        window.subtitle = "Loading page…"
        window.contentView = webView
        webView.load(URLRequest(url: pageURL))
    }

    /// Forget the page's last report, so the toolbar never acts on a page that is gone.
    private func resetPageState() {
        pageLoaded = false
        recording = false
        pending = false
        busy = false
        toggleState = [:]
        applyToggleState()
        waveform.samples = []
        toggles.isEnabled = false
        window.toolbar?.validateVisibleItems()
        endRecordingActivity()
    }

    private func showError(_ message: String, includeLog: Bool = true) {
        resetPageState()
        window.subtitle = "Stopped"
        let heading = NSTextField(wrappingLabelWithString: message)
        heading.font = .boldSystemFont(ofSize: 14)
        var views: [NSView] = [heading]
        if includeLog {
            let log = NSTextField(wrappingLabelWithString: logTail())
            log.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
            log.textColor = .secondaryLabelColor
            log.isSelectable = true
            views.append(log)
        }
        views.append(NSStackView(views: [
            NSButton(title: "Open Log", target: self, action: #selector(openLog)),
            NSButton(title: "Retry", target: self, action: #selector(retry)),
        ]))
        window.contentView = centered(views)
    }

    private func logTail(lines: Int = 15) -> String {
        guard let data = try? Data(contentsOf: logURL) else { return "(no log at \(logURL.path))" }
        let text = String(decoding: data, as: UTF8.self)
        return text.split(separator: "\n", omittingEmptySubsequences: false).suffix(lines).joined(separator: "\n")
    }

    private func centered(_ views: [NSView]) -> NSView {
        let stack = NSStackView(views: views)
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false
        let container = NSView()
        container.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: container.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: container.widthAnchor, constant: -80),
        ])
        return container
    }

    @objc private func openLog() { NSWorkspace.shared.open(logURL) }

    @objc private func retry() {
        showLoading()
        connect()
    }

    // MARK: - Toolbar

    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, .record, .stop, .waveform, .save, .space, .toggles]
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        toolbarDefaultItemIdentifiers(toolbar)
    }

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier id: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        let item = NSToolbarItem(itemIdentifier: id)
        switch id {
        case .record, .stop:
            let label = id == .record ? "Record" : "Stop"
            item.image = NSImage(systemSymbolName: id == .record ? "record.circle" : "stop.fill",
                                 accessibilityDescription: label)
            item.label = label
            item.toolTip = label
            item.target = self
            item.action = id == .record ? #selector(startRecording(_:)) : #selector(stopRecording(_:))
        case .waveform:
            item.view = waveform
            item.label = "Level"
        case .save:
            item.image = NSImage(systemSymbolName: "square.and.arrow.down",
                                 accessibilityDescription: "Save Transcript")
            item.label = "Save Transcript"
            item.toolTip = "Save the English transcript as a text file"
            item.target = self
            item.action = #selector(saveTranscript(_:))
        case .toggles:
            item.view = toggles
            item.label = "View"
        default:
            return nil
        }
        return item
    }

    func validateToolbarItem(_ item: NSToolbarItem) -> Bool {
        guard pageLoaded else { return false }
        switch item.itemIdentifier {
        case .record: return !recording && !pending && !busy
        case .stop: return recording && !pending && !busy
        default: return true
        }
    }

    @objc private func startRecording(_ sender: Any?) { callPage("setRecording(true)") }
    @objc private func stopRecording(_ sender: Any?) { callPage("setRecording(false)") }

    /// Fetch the English transcript from the server (all recording sessions since
    /// it started), then let the user pick where to save it.
    @objc private func saveTranscript(_ sender: Any?) {
        var request = URLRequest(url: transcriptURL)
        request.timeoutInterval = 5
        session.dataTask(with: request) { data, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { self.presentSavePanel(ok ? data : nil) }
        }.resume()
    }

    private func presentSavePanel(_ data: Data?) {
        guard let data else {
            showAlert("Could not read the transcript from the c3po server.")
            return
        }
        guard !String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            showAlert("Nothing transcribed yet.")
            return
        }
        let stamp = DateFormatter()
        stamp.dateFormat = "yyyy-MM-dd HH.mm"
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.plainText]
        panel.directoryURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
        panel.nameFieldStringValue = "C-3PO Transcript \(stamp.string(from: Date())).txt"
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            do {
                try data.write(to: url)
            } catch {
                self.showAlert("Could not save the transcript: \(error.localizedDescription)")
            }
        }
    }

    private func showAlert(_ message: String) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window)
    }

    @objc private func toggleClicked(_ sender: NSSegmentedControl) {
        // With .selectAny the control has already flipped the clicked segment;
        // the one that now disagrees with the page is the one that was clicked.
        for toggle in Toggle.allCases
        where sender.isSelected(forSegment: toggle.rawValue) != (toggleState[toggle] ?? false) {
            callPage(toggle.action)
        }
        // Show the page's real state until it reports the change. An action that
        // fails (e.g. the overlay request) then never leaves the segment lying.
        applyToggleState()
    }

    private func applyToggleState() {
        for toggle in Toggle.allCases {
            toggles.setSelected(toggleState[toggle] ?? false, forSegment: toggle.rawValue)
        }
    }

    /// Calls a `window.c3poNative` action, e.g. "togglePanel()". The trailing
    /// `void 0` matters: evaluateJavaScript fails (WKError 5) on a Promise value.
    private func callPage(_ call: String) {
        webView.evaluateJavaScript("window.c3poNative && window.c3poNative.\(call); void 0")
    }

    // MARK: - Page

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let state = message.body as? [String: Any] else { return }
        if let samples = state["wave"] as? [Double] {
            if recording { waveform.samples = samples }
            return
        }
        recording = state["recording"] as? Bool ?? false
        pending = state["pending"] as? Bool ?? false
        busy = state["busy"] as? Bool ?? false
        toggleState = [
            .settings: state["settings"] as? Bool ?? false,
            .subtitles: state["overlay"] as? Bool ?? false,
            .suggestions: state["panel"] as? Bool ?? false,
        ]
        applyToggleState()
        window.toolbar?.validateVisibleItems()
        if pending {
            window.subtitle = recording ? "Stopping…" : "Starting…"
        } else if busy {
            window.subtitle = "Finishing…"
        } else if recording {
            window.subtitle = "Recording \(state["timer"] as? String ?? "")"
        } else {
            window.subtitle = "Ready"
        }
        if recording {
            beginRecordingActivity()
        } else {
            endRecordingActivity()
            waveform.samples = []
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // An error view may have replaced the page while it was loading.
        guard window.contentView === webView else { return }
        pageLoaded = true
        toggles.isEnabled = true
        window.toolbar?.validateVisibleItems()
        if !recording { window.subtitle = "Ready" }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        showError("Could not load \(pageURL.absoluteString): \(error.localizedDescription)")
    }

    // WebKit can kill the page's process (e.g. under memory pressure); reload
    // rather than leave a blank window.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        resetPageState()
        webView.reload()
    }

    /// Reloading stops a running recording, so it is only offered when idle.
    /// Useful after granting microphone access in System Settings.
    @objc private func reloadPage(_ sender: Any?) { webView.reload() }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(reloadPage(_:)): return pageLoaded && !recording
        case #selector(saveTranscript(_:)): return pageLoaded
        default: return true
        }
    }

    // The page records meeting audio from BlackHole. Grant the microphone to
    // c3po's own page only; nothing else loads here, but refuse it if it does.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        let ours = ["localhost", "127.0.0.1"].contains(origin.host) && origin.port == port
        decisionHandler(ours && type == .microphone ? .grant : .deny)
    }

    // App Nap would throttle this process while the window sits behind Zoom.
    private func beginRecordingActivity() {
        guard recordingActivity == nil else { return }
        recordingActivity = ProcessInfo.processInfo.beginActivity(
            options: .userInitiatedAllowingIdleSystemSleep, reason: "Transcribing a meeting")
    }

    private func endRecordingActivity() {
        if let activity = recordingActivity {
            ProcessInfo.processInfo.endActivity(activity)
            recordingActivity = nil
        }
    }

    // MARK: - Menu

    private func buildMenu() {
        let main = NSMenu()

        let app = NSMenu()
        app.addItem(withTitle: "About C-3PO",
                    action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Hide C-3PO", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(withTitle: "Hide Others",
                    action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
            .keyEquivalentModifierMask = [.command, .option]
        app.addItem(withTitle: "Show All",
                    action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Quit C-3PO", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        // Without these items Cmd-C / Cmd-V / Cmd-A do nothing in the page's text
        // fields (e.g. the pre-meeting context box).
        let file = NSMenu(title: "File")
        file.addItem(withTitle: "Save Transcript…", action: #selector(saveTranscript(_:)), keyEquivalent: "s")
            .target = self

        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
            .keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload Page", action: #selector(reloadPage(_:)), keyEquivalent: "r")
            .target = self

        let windows = NSMenu(title: "Window")
        windows.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windows.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windows.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        for menu in [app, file, edit, view, windows] {
            let item = NSMenuItem()
            item.submenu = menu
            main.addItem(item)
        }
        NSApp.mainMenu = main
        NSApp.windowsMenu = windows
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
