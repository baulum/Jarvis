import AppKit
import ApplicationServices
import Carbon.HIToolbox
import CoreGraphics
import Foundation

/// Keyboard/mouse synthesis via CGEvent. Requires the Accessibility permission.
enum InputHandlers {
    static let maxTypeLength = 5000

    /// Refuses to act when the frontmost app is blocked by policy.
    static func ensureFrontmostAllowed(_ ctx: RequestContext) async throws {
        let front = await MainActor.run { Apps.frontmost() }
        if let front, ctx.policy.isBlocked(bundleId: front.bundleId) {
            throw BridgeError.appBlocked(front.bundleId ?? "?")
        }
    }

    static func requireEventAccess() throws {
        guard AXIsProcessTrusted() else { throw BridgeError.permissionDenied("accessibility") }
    }

    // MARK: Type

    enum TypeSegment: Equatable {
        case text(String)
        case key(CGKeyCode)
    }

    /// Splits text into unicode chunks (max 20 UTF-16 units each, never splitting a character)
    /// and special keys for newlines/tabs.
    static func segments(for text: String, chunkUTF16: Int = 20) -> [TypeSegment] {
        var out: [TypeSegment] = []
        var current = ""
        var currentUnits = 0
        func flush() {
            if !current.isEmpty { out.append(.text(current)) }
            current = ""
            currentUnits = 0
        }
        for ch in text {
            if ch == "\n" || ch == "\r\n" || ch == "\r" {
                flush()
                out.append(.key(CGKeyCode(kVK_Return)))
                continue
            }
            if ch == "\t" {
                flush()
                out.append(.key(CGKeyCode(kVK_Tab)))
                continue
            }
            let units = ch.utf16.count
            if currentUnits + units > chunkUTF16 { flush() }
            current.append(ch)
            currentUnits += units
        }
        flush()
        return out
    }

    static func type(_ ctx: RequestContext) async throws -> JSON {
        try requireEventAccess()
        let text = try ctx.string("text", maxLength: maxTypeLength)
        try await ensureFrontmostAllowed(ctx)
        let segs = segments(for: text)
        let killSwitch = ctx.env.killSwitch
        let typed: Int = try await runBlocking {
            let source = CGEventSource(stateID: .hidSystemState)
            var count = 0
            for seg in segs {
                if killSwitch.isEngaged {
                    throw BridgeError(
                        status: 423, code: "kill_switch_engaged", message: "Typing aborted by kill switch",
                        extra: ["typedCharacters": .int(count)])
                }
                switch seg {
                case .text(let s):
                    var units = Array(s.utf16)
                    guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
                        let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
                    else { throw BridgeError.internalError("Could not create keyboard event") }
                    down.flags = []
                    up.flags = []
                    down.keyboardSetUnicodeString(stringLength: units.count, unicodeString: &units)
                    up.keyboardSetUnicodeString(stringLength: units.count, unicodeString: &units)
                    down.post(tap: .cghidEventTap)
                    up.post(tap: .cghidEventTap)
                    count += s.count
                case .key(let code):
                    try postKey(code, flags: [], source: source)
                    count += 1
                }
                usleep(12_000)
            }
            return count
        }
        return ["ok": true, "typedCharacters": .int(typed)]
    }

    static func postKey(_ code: CGKeyCode, flags: CGEventFlags, source: CGEventSource?) throws {
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true),
            let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)
        else { throw BridgeError.internalError("Could not create keyboard event") }
        down.flags = flags
        up.flags = flags
        down.post(tap: .cghidEventTap)
        usleep(8_000)
        up.post(tap: .cghidEventTap)
    }

    // MARK: Key

    static func pressKey(_ ctx: RequestContext) async throws -> JSON {
        try requireEventAccess()
        let key = try ctx.string("key", maxLength: 32)
        var flags: CGEventFlags = []
        var modNames: [String] = []
        if let mods = ctx.body["modifiers"], !mods.isNull {
            guard let arr = mods.arrayValue else {
                throw BridgeError.badRequest("invalid_param", "'modifiers' must be an array of strings")
            }
            for m in arr {
                guard let name = m.stringValue, let flag = KeyMap.modifier(name) else {
                    throw BridgeError.badRequest(
                        "invalid_param", "Unknown modifier; use cmd, shift, alt (option), ctrl, fn")
                }
                flags.insert(flag)
                modNames.append(name.lowercased())
            }
        }
        try await ensureFrontmostAllowed(ctx)
        let layoutMap = await MainActor.run { KeyMap.currentLayoutMap() }
        guard let resolved = KeyMap.keyCode(for: key, layout: layoutMap) else {
            throw BridgeError.badRequest("unknown_key", "Unknown key '\(key)'")
        }
        let finalFlags = flags.union(resolved.extraFlags)
        try await runBlocking {
            try postKey(resolved.code, flags: finalFlags, source: CGEventSource(stateID: .hidSystemState))
        }
        return ["ok": true, "key": .string(key), "keyCode": .int(Int(resolved.code)), "modifiers": .array(modNames.map { .string($0) })]
    }

    // MARK: Click

    static func click(_ ctx: RequestContext) async throws -> JSON {
        try requireEventAccess()
        guard let x = try ctx.optionalDouble("x"), let y = try ctx.optionalDouble("y") else {
            throw BridgeError.badRequest("missing_param", "'x' and 'y' are required (global screen points, top-left origin)")
        }
        let buttonName = (try ctx.optionalString("button", maxLength: 16) ?? "left").lowercased()
        let count = try ctx.optionalInt("count") ?? 1
        guard (1...3).contains(count) else { throw BridgeError.badRequest("invalid_param", "'count' must be 1, 2 or 3") }

        let point = CGPoint(x: x, y: y)
        var displayCount: UInt32 = 0
        CGGetDisplaysWithPoint(point, 0, nil, &displayCount)
        guard displayCount > 0 else { throw BridgeError.badRequest("invalid_param", "Point is not on any display") }

        let button: CGMouseButton
        let downType: CGEventType
        let upType: CGEventType
        switch buttonName {
        case "left":
            (button, downType, upType) = (.left, .leftMouseDown, .leftMouseUp)
        case "right":
            (button, downType, upType) = (.right, .rightMouseDown, .rightMouseUp)
        case "middle":
            (button, downType, upType) = (.center, .otherMouseDown, .otherMouseUp)
        default:
            throw BridgeError.badRequest("invalid_param", "'button' must be left, right or middle")
        }

        try await ensureFrontmostAllowed(ctx)
        // Also refuse clicks landing on a window of a blocked app.
        if let ownerPid = try await runBlocking({ Windows.ownerPid(atX: x, y: y) }) {
            let bundleId = await MainActor.run { NSRunningApplication(processIdentifier: ownerPid)?.bundleIdentifier }
            if ctx.policy.isBlocked(bundleId: bundleId) { throw BridgeError.appBlocked(bundleId ?? "?") }
        }

        try await runBlocking {
            let source = CGEventSource(stateID: .hidSystemState)
            guard let move = CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: button) else {
                throw BridgeError.internalError("Could not create mouse event")
            }
            move.post(tap: .cghidEventTap)
            usleep(30_000)
            for i in 1...count {
                guard let down = CGEvent(mouseEventSource: source, mouseType: downType, mouseCursorPosition: point, mouseButton: button),
                    let up = CGEvent(mouseEventSource: source, mouseType: upType, mouseCursorPosition: point, mouseButton: button)
                else { throw BridgeError.internalError("Could not create mouse event") }
                down.setIntegerValueField(.mouseEventClickState, value: Int64(i))
                up.setIntegerValueField(.mouseEventClickState, value: Int64(i))
                down.post(tap: .cghidEventTap)
                usleep(15_000)
                up.post(tap: .cghidEventTap)
                usleep(40_000)
            }
        }
        return ["ok": true, "x": .double(x), "y": .double(y), "button": .string(buttonName), "count": .int(count)]
    }
}

/// Key name → virtual key code mapping (layout-aware for printable characters).
enum KeyMap {
    struct Resolved: Equatable {
        var code: CGKeyCode
        var extraFlags: CGEventFlags
    }

    static let special: [String: Int] = [
        "return": kVK_Return, "enter": kVK_Return, "keypadenter": kVK_ANSI_KeypadEnter,
        "tab": kVK_Tab, "space": kVK_Space, "delete": kVK_Delete, "backspace": kVK_Delete,
        "forwarddelete": kVK_ForwardDelete, "escape": kVK_Escape, "esc": kVK_Escape,
        "left": kVK_LeftArrow, "right": kVK_RightArrow, "up": kVK_UpArrow, "down": kVK_DownArrow,
        "home": kVK_Home, "end": kVK_End, "pageup": kVK_PageUp, "pagedown": kVK_PageDown,
        "help": kVK_Help,
        "f1": kVK_F1, "f2": kVK_F2, "f3": kVK_F3, "f4": kVK_F4, "f5": kVK_F5, "f6": kVK_F6,
        "f7": kVK_F7, "f8": kVK_F8, "f9": kVK_F9, "f10": kVK_F10, "f11": kVK_F11, "f12": kVK_F12,
        "f13": kVK_F13, "f14": kVK_F14, "f15": kVK_F15, "f16": kVK_F16, "f17": kVK_F17,
        "f18": kVK_F18, "f19": kVK_F19, "f20": kVK_F20,
    ]

    /// US-ANSI fallback for printable characters.
    static let ansi: [Character: Int] = [
        "a": kVK_ANSI_A, "b": kVK_ANSI_B, "c": kVK_ANSI_C, "d": kVK_ANSI_D, "e": kVK_ANSI_E,
        "f": kVK_ANSI_F, "g": kVK_ANSI_G, "h": kVK_ANSI_H, "i": kVK_ANSI_I, "j": kVK_ANSI_J,
        "k": kVK_ANSI_K, "l": kVK_ANSI_L, "m": kVK_ANSI_M, "n": kVK_ANSI_N, "o": kVK_ANSI_O,
        "p": kVK_ANSI_P, "q": kVK_ANSI_Q, "r": kVK_ANSI_R, "s": kVK_ANSI_S, "t": kVK_ANSI_T,
        "u": kVK_ANSI_U, "v": kVK_ANSI_V, "w": kVK_ANSI_W, "x": kVK_ANSI_X, "y": kVK_ANSI_Y,
        "z": kVK_ANSI_Z, "0": kVK_ANSI_0, "1": kVK_ANSI_1, "2": kVK_ANSI_2, "3": kVK_ANSI_3,
        "4": kVK_ANSI_4, "5": kVK_ANSI_5, "6": kVK_ANSI_6, "7": kVK_ANSI_7, "8": kVK_ANSI_8,
        "9": kVK_ANSI_9, "-": kVK_ANSI_Minus, "=": kVK_ANSI_Equal, "[": kVK_ANSI_LeftBracket,
        "]": kVK_ANSI_RightBracket, ";": kVK_ANSI_Semicolon, "'": kVK_ANSI_Quote,
        ",": kVK_ANSI_Comma, ".": kVK_ANSI_Period, "/": kVK_ANSI_Slash, "\\": kVK_ANSI_Backslash,
        "`": kVK_ANSI_Grave,
    ]

    static func modifier(_ name: String) -> CGEventFlags? {
        switch name.lowercased() {
        case "cmd", "command", "⌘": return .maskCommand
        case "shift", "⇧": return .maskShift
        case "alt", "option", "opt", "⌥": return .maskAlternate
        case "ctrl", "control", "⌃": return .maskControl
        case "fn", "function": return .maskSecondaryFn
        default: return nil
        }
    }

    static func keyCode(for name: String, layout: [Character: Int]) -> Resolved? {
        let lower = name.lowercased()
        if let code = special[lower] { return Resolved(code: CGKeyCode(code), extraFlags: []) }
        guard name.count == 1, let ch = name.first else { return nil }
        if let code = layout[ch] ?? ansi[ch] { return Resolved(code: CGKeyCode(code), extraFlags: []) }
        // Uppercase letters: lowercase key + shift.
        if let l = name.lowercased().first, l != ch, let code = layout[l] ?? ansi[l] {
            return Resolved(code: CGKeyCode(code), extraFlags: .maskShift)
        }
        return nil
    }

    /// Builds a character → key code map for the current keyboard layout (e.g. German QWERTZ).
    @MainActor
    static func currentLayoutMap() -> [Character: Int] {
        guard let source = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
            let raw = TISGetInputSourceProperty(source, kTISPropertyUnicodeKeyLayoutData)
        else { return [:] }
        let data = Unmanaged<CFData>.fromOpaque(raw).takeUnretainedValue() as Data
        var map: [Character: Int] = [:]
        data.withUnsafeBytes { buf in
            guard let layout = buf.baseAddress?.assumingMemoryBound(to: UCKeyboardLayout.self) else { return }
            for code in 0..<128 {
                var deadKeyState: UInt32 = 0
                var length = 0
                var chars = [UniChar](repeating: 0, count: 4)
                let status = UCKeyTranslate(
                    layout, UInt16(code), UInt16(kUCKeyActionDisplay), 0, UInt32(LMGetKbdType()),
                    OptionBits(1 << kUCKeyTranslateNoDeadKeysBit), &deadKeyState, chars.count, &length, &chars)
                guard status == noErr, length > 0 else { continue }
                let s = String(utf16CodeUnits: chars, count: length)
                guard s.count == 1, let ch = s.first, map[ch] == nil else { continue }
                // Skip keypad codes so digits map to the main row.
                if (65...92).contains(code) { continue }
                map[ch] = code
            }
        }
        return map
    }
}
