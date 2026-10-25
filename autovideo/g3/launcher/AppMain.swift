import AppKit
import Foundation

final class LauncherDelegate: NSObject, NSApplicationDelegate {
    private var launching = false

    func applicationDidFinishLaunching(_ notification: Notification) { launch() }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        launch()
        return true
    }

    private func launch() {
        guard !launching else { return }
        launching = true
        guard let resources = Bundle.main.resourceURL else {
            showError("启动器资源不可用，请重新构建。", log: nil)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            var result: [String: Any] = [:]
            do {
                let configURL = resources.appendingPathComponent("launcher.json")
                let data = try Data(contentsOf: configURL)
                guard let config = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let python = config["python"] as? String else {
                    throw NSError(domain: "AutoVideo", code: 1, userInfo: [NSLocalizedDescriptionKey: "启动配置不完整。"])
                }
                let process = Process()
                process.executableURL = URL(fileURLWithPath: python)
                process.arguments = [resources.appendingPathComponent("launch.py").path, "--config", configURL.path]
                let output = Pipe()
                process.standardOutput = output
                process.standardError = FileHandle.nullDevice
                try process.run()
                let body = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
                    result = object
                }
                if result["log_path"] == nil, let project = config["project_dir"] as? String {
                    result["log_path"] = (project as NSString).appendingPathComponent("g3/data/launcher/server.log")
                }
                if process.terminationStatus != 0 && result["message"] == nil {
                    result["message"] = "启动失败，请检查本机 Python 环境和启动日志。"
                }
            } catch { result["message"] = error.localizedDescription }
            let answer = result
            DispatchQueue.main.async {
                if answer["status"] as? String == "ready", let address = answer["url"] as? String,
                   let url = URL(string: address), NSWorkspace.shared.open(url) {
                    NSApplication.shared.terminate(nil)
                } else {
                    self.showError(answer["message"] as? String ?? "网页未能打开，请检查默认浏览器。", log: answer["log_path"] as? String)
                }
            }
        }
    }

    private func showError(_ message: String, log: String?) {
        NSApplication.shared.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "AutoVideo 未能打开"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.addButton(withTitle: "好")
        if let log = log, FileManager.default.fileExists(atPath: log) {
            alert.addButton(withTitle: "查看日志")
            if alert.runModal() == .alertSecondButtonReturn { NSWorkspace.shared.open(URL(fileURLWithPath: log)) }
        } else { alert.runModal() }
        NSApplication.shared.terminate(nil)
    }
}

let application = NSApplication.shared
let delegate = LauncherDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
