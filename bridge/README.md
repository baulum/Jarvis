# JarvisBridge

JarvisBridge is the native macOS component of J.A.R.V.I.S. It is a small background app with no Dock icon (`LSUIElement`). It exposes a local, token-protected HTTP/JSON API on `127.0.0.1:8766`, which the Python core (`127.0.0.1:8765`) uses to control the Mac.

Every endpoint calls real macOS APIs:

- Accessibility (`AXUIElement`)
- ScreenCaptureKit
- `NSWorkspace` and `NSRunningApplication`
- `CGWindowListCopyWindowInfo`
- `CGEvent`
- `NSPasteboard`
- Spotlight (`mdfind`)
- `NSAppleScript`
- `/usr/bin/shortcuts`

The HTTP server is a minimal HTTP/1.1 implementation built on `Network.framework` (`NWListener`). The package has **no third-party dependencies**.

- Swift 6 language mode with strict concurrency, swift-tools-version 6.0, macOS 14+
- Targets:
  - `JarvisBridgeCore`: library with the server, router, security and handlers
  - `JarvisBridge`: the executable
  - `JarvisBridgeCoreTests`: XCTest suite

## Build & run

```bash
cd bridge
swift build -c release          # compile
swift test                      # unit + loopback integration tests
scripts/build-app.sh            # -> build/JarvisBridge.app (ad-hoc signed, hardened runtime)

# run in the foreground (logs to stderr)
build/JarvisBridge.app/Contents/MacOS/JarvisBridge

# or as a LaunchAgent (starts at login, KeepAlive, logs to ~/Library/Logs/Jarvis/bridge.log)
scripts/install-launchagent.sh
scripts/uninstall-launchagent.sh
```

`build-app.sh` signs ad-hoc by default with `codesign --force --sign - --options runtime --timestamp=none` and the entitlement `com.apple.security.automation.apple-events`, which AppleScript needs under the hardened runtime. To sign with a real identity instead, set `SIGN_IDENTITY="Apple Development: …" scripts/build-app.sh`.

If port 8766 is already in use, the process exits with status 1. Under the LaunchAgent, launchd then retries every 10 s.

## macOS permissions (manual step)

TCC permissions are granted to **JarvisBridge.app** (bundle id `com.jarvis.bridge`):

| Permission | Needed for | Where |
|---|---|---|
| Accessibility | `/ui/*`, `/input/*`, focused element in `/frontmost` | System Settings › Privacy & Security › Accessibility |
| Screen Recording | `/screenshot`, window titles in `/windows` | System Settings › Privacy & Security › Screen & System Audio Recording |
| Automation | `/applescript` (one prompt per target app) | prompted on first use |

- `POST /permissions/request {"kind":"accessibility"|"screenRecording"}` opens the system prompt. The bridge never opens a prompt on its own: endpoints that lack a permission return `403 {"error":"permission_denied","permission":"…"}`.
- **Ad-hoc signatures change on every rebuild.** After a rebuild, macOS may treat the app as a new one, so you may have to remove the old JarvisBridge entry and grant the permissions again. A stable signing identity (`SIGN_IDENTITY`) avoids this.
- When you start the binary from a terminal, macOS charges the permissions to the terminal app, not to JarvisBridge.app. For real use, run it through the LaunchAgent or with `open build/JarvisBridge.app`.

## Security model

| Mechanism | Details |
|---|---|
| Binding | Listens only on `127.0.0.1` (`requiredLocalEndpoint`, `acceptLocalOnly`). Any connection whose remote endpoint is not loopback (127.0.0.0/8, ::1) is dropped. |
| Host/Origin | `Host` must be `127.0.0.1`, `localhost` or `[::1]` (optional `:8766`); otherwise **421**. This guards against DNS rebinding. Any request with an `Origin` header is refused with **403**, because browsers are not valid clients. |
| Auth | `Authorization: Bearer <token>` on every endpoint except `GET /health`. The comparison is constant-time. |
| Token | `~/Library/Application Support/Jarvis/bridge.token` (directory 0700, file 0600). If it is missing, the bridge generates 32 bytes with `SecRandomCopyBytes`, hex-encodes them and writes the file with `O_EXCL`. Tokens that are malformed or shorter than 32 characters are regenerated. |
| Kill switch | `POST /killswitch {"engaged":bool}`. It is also engaged while `~/Library/Application Support/Jarvis/KILL` exists; this is checked on every request. While engaged, acting and sensitive-read endpoints return **423** `{"error":"kill_switch_engaged"}`. The API state is persisted (`bridge-killswitch.engaged`), so it survives restarts. The API cannot clear the KILL file. Typing in progress aborts between chunks once the switch is engaged. |
| Policy | `~/Library/Application Support/Jarvis/bridge-policy.json` (see below). It is created with defaults if missing and reloaded when its mtime or size changes. If the file cannot be parsed, everything fails closed. |
| Rate limit | Token bucket with 20 requests/s and a burst of 20, applied to acting and sensitive-read endpoints. Over the limit: **429** with `Retry-After: 1`. |
| Audit log | `~/Library/Logs/Jarvis/bridge-audit.jsonl` (0600, rotates at 20 MB). Fields: `ts`, `action`, `method`, `path`, `params`, `result`, `status`, `error`, `durationMs`. These parameter keys are replaced by `{"redacted":true,"length":n}`: `text`, `value`, `source`, `input`, `token`, `password`. Neither the token nor clipboard contents are ever logged. |
| HTTP limits | Body ≤ 10 MB (**413**). Headers ≤ 32 KB (**431**). Content-Length only: chunked encoding gets **501**. 15 s to receive a request (**408**), 30 s keep-alive idle timeout, at most 64 connections. |

### Policy file

```json
{
  "allowedActions": ["permissions.request","apps.list","apps.launch","apps.activate","apps.quit",
    "windows.list","frontmost","ui.tree","ui.press","ui.setValue","input.type","input.key",
    "input.click","screenshot","clipboard.read","clipboard.write","files.search","files.move",
    "files.rename","files.mkdir","files.reveal","applescript","shortcuts.list","shortcuts.run","openUrl"],
  "allowAppleScript": false,
  "allowedFileRoots": ["~/Documents","~/Desktop","~/Downloads","~/Developer"],
  "blockedApps": ["com.apple.keychainaccess","com.apple.systempreferences"]
}
```

- **Actions:** an action runs only if it is listed in `allowedActions`. AppleScript also needs `"allowAppleScript": true`. `health`, `killswitch` and `policy.read` are always allowed. A denied action returns **403** `action_not_allowed` (or `applescript_disabled`).
- **File roots:** `files.move`, `files.rename`, `files.mkdir` and `files.reveal` only accept paths inside `allowedFileRoots`. Both source and destination are checked. Path checking works like this:
  1. Any `..` component is rejected outright (`path_traversal`).
  2. The deepest existing ancestor is resolved with `realpath`, so a symlink that points outside a root is detected (`path_not_allowed`).
  3. The roots themselves are resolved the same way.
  4. A root itself can only be the target of `reveal`.
- **Blocked apps:** `ui.tree`, `ui.press`, `ui.setValue` and screenshots of single windows are refused (**403** `app_blocked`) when the target app is in `blockedApps`. `input.type`, `input.key` and `input.click` are refused when the frontmost app is blocked. Clicks are also refused when the window under the cursor belongs to a blocked app.

### Endpoint classes

| Class | Endpoints | Kill switch | Rate limit |
|---|---|---|---|
| open | `GET /health` | – | – |
| status | `GET /apps`, `GET /windows`, `GET /frontmost`, `GET /shortcuts`, `GET/POST /killswitch`, `GET /policy` | allowed | – |
| sensitive read | `POST /ui/tree`, `POST /screenshot`, `GET /clipboard`, `POST /files/search` | **423** | yes |
| act | all other endpoints | **423** | yes |

## API

All bodies are JSON objects. Errors have the form `{"error":"code","message":"…", …extra}` and use a matching HTTP status. Coordinates are global screen points with a top-left origin, as used by CGEvent and the AX frames.

Apps are selected with one of `"pid"`, `"bundleId"` or `"name"` (precedence in that order). Name matching is case-insensitive against the localized name or the `.app` file name. For `/ui/*`, the frontmost app is used if no selector is given.

| Method & path | Body / query | Response |
|---|---|---|
| `GET /health` | – | `{"ok":true,"version":"0.1.0","pid":…,"killSwitch":bool,"permissions":{"accessibility":bool,"screenRecording":bool}}` |
| `POST /permissions/request` | `{"kind":"accessibility"\|"screenRecording"}` | `{"ok":true,"kind":…,"permissions":{…}}` |
| `GET /apps` | – | `[{"name","bundleId","pid","active","hidden","path"}]` (regular apps only) |
| `POST /apps/launch` | `{"bundleId"?, "name"?, "activate"?: true}` | `{"ok":true,"launched":"/path/App.app","app":{…}}` |
| `POST /apps/activate` | selector | `{"ok":true,"activated":bool,"app":{…}}` |
| `POST /apps/quit` | selector | `{"ok":bool,"terminateRequested":bool,"app":{…}}` (graceful `terminate()` only) |
| `GET /windows` | `?allLayers=true`, `?includeOffscreen=true` | `[{"windowId","ownerName","ownerPid","title","bounds":{x,y,w,h},"layer","onScreen"}]`. Layer 0 and on-screen only by default. `title` is null without Screen Recording. |
| `GET /frontmost` | – | app fields (`name`, `bundleId`, `pid`, …) plus `accessibility`, `focusedWindowTitle`, `focusedWindow{title,frame}`, `focusedElement{role,subrole,title,description}`, `frontWindow` |
| `POST /ui/tree` | selector, `"maxDepth"` (6, max 12), `"maxNodes"` (400, max 2000) | Root node (the application element, `id:"0"`) plus `app`, `nodeCount`, `truncated`. Node fields: `id` (path such as `"0.3.1"`), `role`, `subrole`, `title`, `value` (≤ 200 chars), `description`, `identifier`, `enabled`, `focused`, `frame`, `actions`, `children`. Nodes that were cut off carry `childCount`. Missing fields are omitted. |
| `POST /ui/press` | selector plus `"path"`, or `"role"`/`"title"` (first match, breadth-first; `title` also matches description or value); optional `"action"` (default `AXPress`) | `{"ok":true,"path","action","element":{…}}` |
| `POST /ui/set-value` | selector, `"path"` (or role/title), `"value"` | `{"ok":true,"path","role","length"}` |
| `POST /input/type` | `{"text"}` (≤ 5000 chars) | `{"ok":true,"typedCharacters":n}`. Uses unicode CGEvents in chunks of ≤ 20 UTF-16 units; `\n` becomes Return and `\t` becomes Tab. |
| `POST /input/key` | `{"key":"return"\|"tab"\|"escape"\|"a"\|"f5"\|…, "modifiers":["cmd","shift","alt","ctrl","fn"]}` | `{"ok":true,"key","keyCode","modifiers"}`. Characters are mapped through the current keyboard layout (e.g. QWERTZ). |
| `POST /input/click` | `{"x","y","button":"left"\|"right"\|"middle","count":1..3}` | `{"ok":true,…}` |
| `POST /screenshot` | `{"displayId"?, "windowId"?, "maxWidth"?:1600}` | `{"png_base64","width","height","scale","displayId"\|"windowId"}` (ScreenCaptureKit, downscaled to `maxWidth`) |
| `GET /clipboard` | – | `{"text":…\|null,"hasText":bool,"types":[…]}` |
| `POST /clipboard` | `{"text"}` (≤ 1 M chars) | `{"ok":true,"length":n}` |
| `POST /files/search` | `{"query", "onlyIn"?, "limit"?:50 (max 500), "match"?:"any"\|"name"}` | `[{"path","name","kind","type","modified","size"}]`. `kind` is `file`, `directory`, `package` or `symlink`. `match:"name"` searches file names only. |
| `POST /files/move` | `{"from","to"}` | `{"ok":true,"from","to"}`. If `to` is an existing directory, the item moves into it. **409** if the destination exists. |
| `POST /files/rename` | `{"path","newName"}` | `{"ok":true,"from","to"}` |
| `POST /files/mkdir` | `{"path"}` (intermediate directories are created) | `{"ok":true,"path","created":bool}` |
| `POST /files/reveal` | `{"path"}` | `{"ok":true,"path"}` (Finder `activateFileViewerSelecting`) |
| `POST /applescript` | `{"source"}` | `{"ok":true,"result":string\|null}`. Errors: **422** `applescript_error` with `errorNumber`. |
| `GET /shortcuts` | – | `["Shortcut name", …]` |
| `POST /shortcuts/run` | `{"name","input"?: text}` | `{"ok":true,"name","output":string\|null}`. Uses a 60 s timeout (**504**); a failure returns **422** `shortcut_failed`. |
| `POST /open-url` | `{"url"}` (http/https/mailto only) | `{"ok":true,"url"}` |
| `GET /killswitch` | – | `{"killSwitch","apiEngaged","fileEngaged"}` |
| `POST /killswitch` | `{"engaged":bool}` | the same, plus `"ok"` |
| `GET /policy` | – | `{"policy":{…},"file":…,"error":…}` |

Common error codes:

- `unauthorized` (401)
- `kill_switch_engaged` (423)
- `action_not_allowed`, `applescript_disabled`, `app_blocked`, `path_traversal`, `path_not_allowed`, `permission_denied`, `scheme_not_allowed`, `origin_not_allowed` (403)
- `rate_limited` (429)
- `missing_param`, `invalid_param`, `invalid_json`, `unknown_key` (400)
- `app_not_found`, `element_not_found`, `window_not_found` (404)
- `destination_exists` (409)
- `ax_error`, `not_settable`, `launch_failed`, `shortcut_failed` (422)

### Example

```bash
TOKEN=$(cat ~/Library/Application\ Support/Jarvis/bridge.token)
curl -s http://127.0.0.1:8766/health
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8766/apps
curl -s -H "Authorization: Bearer $TOKEN" -X POST http://127.0.0.1:8766/files/search \
     -d '{"query":"Rechnung","onlyIn":"~/Documents","limit":10}'
curl -s -H "Authorization: Bearer $TOKEN" -X POST http://127.0.0.1:8766/killswitch -d '{"engaged":true}'
touch ~/Library/Application\ Support/Jarvis/KILL     # hard stop, independent of the API
```

## Layout

```
bridge/
  Package.swift
  Sources/JarvisBridge/main.swift                    NSApplication (.accessory), signal handling
  Sources/JarvisBridgeCore/
    BridgeRuntime.swift                              wiring
    HTTP/      HTTPParser, HTTPMessage, HTTPServer (NWListener)
    Routing/   Router, RequestPipeline (auth → kill switch → policy → rate limit → handler → audit), Routes
    Security/  Auth/TokenStore, KillSwitch, Policy, PathGuard, RateLimiter, AuditLog, BridgePaths
    Handlers/  Apps, Windows, AX/UI, Input, Screenshot, Clipboard, Files, Automation, System
    Support/   JSON, ProcessRunner, Log
  Tests/JarvisBridgeCoreTests/
  Support/Info.plist, JarvisBridge.entitlements
  scripts/build-app.sh, install-launchagent.sh, uninstall-launchagent.sh
```
