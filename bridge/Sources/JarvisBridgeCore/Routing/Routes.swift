import Foundation

/// The complete API surface of the bridge.
public enum BridgeRoutes {
    public static func all() -> [Route] {
        typealias A = BridgeAction
        return [
            // Open / status
            Route("GET", "/health", action: A.health, access: .open, handler: SystemHandlers.health),
            Route("GET", "/killswitch", action: A.killSwitch, access: .status, handler: SystemHandlers.killSwitchStatus),
            Route("POST", "/killswitch", action: A.killSwitch, access: .status, handler: SystemHandlers.killSwitch),
            Route("GET", "/policy", action: A.policyRead, access: .status, handler: SystemHandlers.policy),
            Route("POST", "/permissions/request", action: A.permissionsRequest, access: .act, handler: SystemHandlers.requestPermission),

            // Apps & windows
            Route("GET", "/apps", action: A.appsList, access: .status, handler: AppHandlers.list),
            Route("POST", "/apps/launch", action: A.appsLaunch, access: .act, handler: AppHandlers.launch),
            Route("POST", "/apps/activate", action: A.appsActivate, access: .act, handler: AppHandlers.activate),
            Route("POST", "/apps/quit", action: A.appsQuit, access: .act, handler: AppHandlers.quit),
            Route("GET", "/windows", action: A.windowsList, access: .status, handler: WindowHandlers.list),
            Route("GET", "/frontmost", action: A.frontmost, access: .status, handler: WindowHandlers.frontmost),

            // Accessibility
            Route("POST", "/ui/tree", action: A.uiTree, access: .sensitiveRead, handler: UIHandlers.tree),
            Route("POST", "/ui/press", action: A.uiPress, access: .act, handler: UIHandlers.press),
            Route("POST", "/ui/set-value", action: A.uiSetValue, access: .act, handler: UIHandlers.setValue),

            // Input
            Route("POST", "/input/type", action: A.inputType, access: .act, handler: InputHandlers.type),
            Route("POST", "/input/key", action: A.inputKey, access: .act, handler: InputHandlers.pressKey),
            Route("POST", "/input/click", action: A.inputClick, access: .act, handler: InputHandlers.click),

            // Screen & clipboard
            Route("POST", "/screenshot", action: A.screenshot, access: .sensitiveRead, handler: ScreenshotHandler.capture),
            Route("GET", "/clipboard", action: A.clipboardRead, access: .sensitiveRead, handler: ClipboardHandlers.read),
            Route("POST", "/clipboard", action: A.clipboardWrite, access: .act, handler: ClipboardHandlers.write),

            // Files
            Route("POST", "/files/search", action: A.filesSearch, access: .sensitiveRead, handler: FileHandlers.search),
            Route("POST", "/files/move", action: A.filesMove, access: .act, handler: FileHandlers.move),
            Route("POST", "/files/rename", action: A.filesRename, access: .act, handler: FileHandlers.rename),
            Route("POST", "/files/mkdir", action: A.filesMkdir, access: .act, handler: FileHandlers.mkdir),
            Route("POST", "/files/reveal", action: A.filesReveal, access: .act, handler: FileHandlers.reveal),

            // Automation
            Route("POST", "/applescript", action: A.appleScript, access: .act, handler: AutomationHandlers.appleScript),
            Route("GET", "/shortcuts", action: A.shortcutsList, access: .status, handler: AutomationHandlers.listShortcuts),
            Route("POST", "/shortcuts/run", action: A.shortcutsRun, access: .act, handler: AutomationHandlers.runShortcut),
            Route("POST", "/open-url", action: A.openURL, access: .act, handler: AutomationHandlers.openURL),
        ]
    }
}
