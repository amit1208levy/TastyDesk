import Capacitor
import Foundation
import UIKit
import UniformTypeIdentifiers

/// The "Tasty Desk Sync" folder in iCloud Drive, as the iPhone app sees it.
///
/// The user picks the folder once (the Files picker). The app keeps a bookmark
/// to it, so later reads and writes need no picker and no iCloud entitlement:
/// the access is the one the user granted. Only `*.tdsync` files are read,
/// and only one named by the app is written. What is in them is encrypted
/// before it gets here (core/devicesync.py).
@objc(ICloudFolderPlugin)
public class ICloudFolderPlugin: CAPPlugin, CAPBridgedPlugin, UIDocumentPickerDelegate {
    public let identifier = "ICloudFolderPlugin"
    public let jsName = "ICloudFolder"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "pick", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "current", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "list", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "write", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "forget", returnType: CAPPluginReturnPromise),
    ]

    private let bookmarkKey = "tastydesk.syncFolderBookmark"
    private let suffix = ".tdsync"
    private var picking: CAPPluginCall?

    // MARK: choosing the folder

    @objc func pick(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard let presenter = self.bridge?.viewController else {
                call.reject("The app is not ready to show the Files picker.")
                return
            }
            let picker = UIDocumentPickerViewController(forOpeningContentTypes: [UTType.folder])
            picker.delegate = self
            picker.allowsMultipleSelection = false
            self.picking = call
            presenter.present(picker, animated: true)
        }
    }

    public func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        guard let call = picking else { return }
        picking = nil
        guard let url = urls.first else {
            call.resolve([:])
            return
        }
        let granted = url.startAccessingSecurityScopedResource()
        defer { if granted { url.stopAccessingSecurityScopedResource() } }
        do {
            let bookmark = try url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil)
            UserDefaults.standard.set(bookmark, forKey: bookmarkKey)
            call.resolve(["name": url.lastPathComponent])
        } catch {
            call.reject("Could not keep access to that folder.")
        }
    }

    public func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        picking?.resolve([:])
        picking = nil
    }

    @objc func current(_ call: CAPPluginCall) {
        if let folder = try? folder() {
            call.resolve(["name": folder.lastPathComponent])
        } else {
            call.resolve([:])
        }
    }

    @objc func forget(_ call: CAPPluginCall) {
        UserDefaults.standard.removeObject(forKey: bookmarkKey)
        call.resolve()
    }

    // MARK: the files

    @objc func list(_ call: CAPPluginCall) {
        DispatchQueue.global(qos: .utility).async {
            guard let folder = try? self.folder() else {
                call.reject("No folder chosen.")
                return
            }
            let granted = folder.startAccessingSecurityScopedResource()
            defer { if granted { folder.stopAccessingSecurityScopedResource() } }

            var files: [[String: String]] = []
            var waiting = 0
            var failure: Error?
            var coordination: NSError?
            NSFileCoordinator().coordinate(readingItemAt: folder, options: [], error: &coordination) { dir in
                do {
                    let names = try FileManager.default.contentsOfDirectory(atPath: dir.path)
                    for name in names {
                        if name.hasPrefix(".") && name.hasSuffix(self.suffix + ".icloud") {
                            // Still in the cloud: ask for it, read it next round.
                            let real = String(name.dropFirst().dropLast(".icloud".count))
                            try? FileManager.default.startDownloadingUbiquitousItem(
                                at: dir.appendingPathComponent(real))
                            waiting += 1
                            continue
                        }
                        guard name.hasSuffix(self.suffix), !name.hasPrefix(".") else { continue }
                        let file = dir.appendingPathComponent(name)
                        let status = try? file.resourceValues(forKeys: [.ubiquitousItemDownloadingStatusKey])
                        if let state = status?.ubiquitousItemDownloadingStatus, state != .current {
                            try? FileManager.default.startDownloadingUbiquitousItem(at: file)
                            waiting += 1
                            continue
                        }
                        let data = try Data(contentsOf: file)
                        files.append(["name": name, "data": data.base64EncodedString()])
                    }
                } catch {
                    failure = error
                }
            }
            if coordination != nil || failure != nil {
                call.reject("Could not read the sync folder.")
                return
            }
            call.resolve(["files": files, "waiting": waiting])
        }
    }

    @objc func write(_ call: CAPPluginCall) {
        // Only a sync file of the app's own, never a path.
        guard let name = call.getString("name"), let encoded = call.getString("data"),
              let data = Data(base64Encoded: encoded),
              name.hasSuffix(suffix), !name.hasPrefix("."), !name.contains("/"), name.count <= 64
        else {
            call.reject("Not a sync file.")
            return
        }
        DispatchQueue.global(qos: .utility).async {
            guard let folder = try? self.folder() else {
                call.reject("No folder chosen.")
                return
            }
            let granted = folder.startAccessingSecurityScopedResource()
            defer { if granted { folder.stopAccessingSecurityScopedResource() } }

            let target = folder.appendingPathComponent(name)
            var failure: Error?
            var coordination: NSError?
            NSFileCoordinator().coordinate(writingItemAt: target, options: .forReplacing, error: &coordination) { url in
                do {
                    try data.write(to: url, options: .atomic)
                } catch {
                    failure = error
                }
            }
            if coordination != nil || failure != nil {
                call.reject("Could not write to the sync folder.")
                return
            }
            call.resolve()
        }
    }

    // MARK: -

    private func folder() throws -> URL? {
        guard let bookmark = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        let url = try URL(resolvingBookmarkData: bookmark, options: [], relativeTo: nil, bookmarkDataIsStale: &stale)
        if stale {
            let granted = url.startAccessingSecurityScopedResource()
            defer { if granted { url.stopAccessingSecurityScopedResource() } }
            if let fresh = try? url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil) {
                UserDefaults.standard.set(fresh, forKey: bookmarkKey)
            }
        }
        return url
    }
}
