package systems.pje.jarvis_mobile.phone

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ComponentName
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Base64
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume

/** A failure the core relays to Sir in plain words; [code] is mapped to German text on the server. */
class PhoneException(val code: String, message: String = "") : Exception(message)

/** Adapter: AccessibilityNodeInfo as UiNodeView. */
class AxNode(val info: AccessibilityNodeInfo) : UiNodeView {
    override val className get() = info.className?.toString()
    override val text get() = info.text?.toString()
    override val contentDescription get() = info.contentDescription?.toString()
    override val viewId get() = info.viewIdResourceName
    override val clickable get() = info.isClickable
    override val longClickable get() = info.isLongClickable
    override val scrollable get() = info.isScrollable
    override val editable get() = info.isEditable
    override val focused get() = info.isFocused
    override val checkable get() = info.isCheckable
    override val checked get() = info.isChecked
    override val enabled get() = info.isEnabled
    override val visible get() = info.isVisibleToUser
    override val password get() = info.isPassword || UiTree.isPasswordInputType(info.inputType)
    override val bounds: IntArray get() = Rect().also { info.getBoundsInScreen(it) }.let { intArrayOf(it.left, it.top, it.right, it.bottom) }
    override val childCount get() = info.childCount
    override fun child(index: Int): UiNodeView? = info.getChild(index)?.let { AxNode(it) }
}

/**
 * "Handy-Steuerung durch Jarvis": the accessibility service that executes Jarvis' phone_* commands (read the
 * screen, tap, type, scroll, system keys, screenshot). It only acts on commands that arrive through the paired,
 * end-to-end encrypted push channel (see PhoneControl) and never reads or fills password fields. Sir switches it
 * on and off in Android's accessibility settings.
 */
class PhoneControlService : AccessibilityService() {
    companion object {
        @Volatile var instance: PhoneControlService? = null
            private set

        /** Is the service switched on in the system settings? (Independent of whether the process is bound yet.) */
        fun isEnabled(context: Context): Boolean {
            if (instance != null) return true
            val me = ComponentName(context, PhoneControlService::class.java)
            val enabled = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            return enabled.split(':').any { ComponentName.unflattenFromString(it) == me }
        }
    }

    /** id → fingerprint of the last ui_tree, to notice a changed screen before acting on an old id */
    @Volatile private var fingerprints: Map<String, String> = emptyMap()

    override fun onServiceConnected() { instance = this }
    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit // commands only, no event processing
    override fun onInterrupt() = Unit
    override fun onUnbind(intent: android.content.Intent?): Boolean { instance = null; return super.onUnbind(intent) }
    override fun onDestroy() { instance = null; super.onDestroy() }

    // ------------------------------------------------------------------ reading
    private suspend fun root(): AccessibilityNodeInfo {
        repeat(8) {
            rootInActiveWindow?.let { return it }
            delay(200) // window is switching (app start, transition)
        }
        throw PhoneException("action_failed", "Kein Fenster lesbar (Bildschirm gesperrt oder App sperrt Bedienungshilfen).")
    }

    suspend fun foregroundPackage(): String? = runCatching { root().packageName?.toString() }.getOrNull()

    suspend fun uiTree(): org.json.JSONObject {
        val root = root()
        val built = UiTree.build(AxNode(root))
        fingerprints = built.fingerprints
        return org.json.JSONObject()
            .put("package", root.packageName?.toString())
            .put("screen", org.json.JSONObject().put("width", resources.displayMetrics.widthPixels).put("height", resources.displayMetrics.heightPixels))
            .put("nodes", built.nodes)
            .put("truncated", built.truncated)
    }

    /** Finds the element for [id]; refuses when the screen changed since ui_tree. */
    private suspend fun element(id: String): AxNode {
        val node = UiTree.find(AxNode(root()), id) as AxNode? ?: throw PhoneException("element_not_found")
        val known = fingerprints[id]
        if (known != null && known != UiTree.fingerprint(node)) throw PhoneException("stale_element")
        return node
    }

    // ------------------------------------------------------------------ acting
    suspend fun tap(elementId: String?, x: Float?, y: Float?) {
        if (elementId != null) {
            val node = element(elementId)
            var target: AccessibilityNodeInfo? = node.info
            var hops = 0
            while (target != null && !target.isClickable && hops++ < 4) target = target.parent
            if (target != null && target.isClickable && target.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return
            val b = node.bounds
            if (b[2] <= b[0] || b[3] <= b[1]) throw PhoneException("action_failed", "Element hat keine Fläche zum Antippen.")
            return gestureTap(((b[0] + b[2]) / 2).toFloat(), ((b[1] + b[3]) / 2).toFloat())
        }
        gestureTap(x!!, y!!)
    }

    private suspend fun gestureTap(x: Float, y: Float) {
        val m = resources.displayMetrics
        if (x < 0 || y < 0 || x > m.widthPixels || y > m.heightPixels) {
            throw PhoneException("bad_request", "Position liegt außerhalb des Bildschirms (${m.widthPixels}×${m.heightPixels}).")
        }
        val path = Path().apply { moveTo(x, y) }
        dispatch(GestureDescription.StrokeDescription(path, 0, 60))
    }

    suspend fun scroll(direction: String, elementId: String?) {
        val m = resources.displayMetrics
        val area = if (elementId != null) element(elementId).bounds.let { Rect(it[0], it[1], it[2], it[3]) } else Rect(0, 0, m.widthPixels, m.heightPixels)
        if (area.width() < 40 || area.height() < 40) throw PhoneException("action_failed", "Bereich zu klein zum Scrollen.")
        val cx = area.exactCenterX(); val cy = area.exactCenterY()
        val dx = area.width() * 0.3f; val dy = area.height() * 0.3f
        // "down" = show content further down = finger moves up
        val (x0, y0, x1, y1) = when (direction) {
            "down" -> listOf(cx, cy + dy, cx, cy - dy)
            "up" -> listOf(cx, cy - dy, cx, cy + dy)
            "right" -> listOf(cx + dx, cy, cx - dx, cy)
            "left" -> listOf(cx - dx, cy, cx + dx, cy)
            else -> throw PhoneException("bad_request", "direction: up, down, left oder right")
        }
        val path = Path().apply { moveTo(x0, y0); lineTo(x1, y1) }
        dispatch(GestureDescription.StrokeDescription(path, 0, 350))
    }

    private suspend fun dispatch(stroke: GestureDescription.StrokeDescription) {
        val ok = suspendCancellableCoroutine { cont ->
            val accepted = dispatchGesture(
                GestureDescription.Builder().addStroke(stroke).build(),
                object : GestureResultCallback() {
                    override fun onCompleted(g: GestureDescription?) { if (cont.isActive) cont.resume(true) }
                    override fun onCancelled(g: GestureDescription?) { if (cont.isActive) cont.resume(false) }
                },
                null,
            )
            if (!accepted && cont.isActive) cont.resume(false)
        }
        if (!ok) throw PhoneException("action_failed", "Die Geste wurde vom System abgebrochen.")
    }

    /** Sets the text of a field; password fields are refused whatever the way they were addressed. */
    suspend fun typeText(text: String, elementId: String?, append: Boolean) {
        val target: AccessibilityNodeInfo = if (elementId != null) element(elementId).info
        else root().findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: throw PhoneException("no_input_field")
        val view = AxNode(target)
        if (view.password) throw PhoneException("password_field")
        if (!target.isEditable) throw PhoneException("no_input_field", "Das Element ist kein Eingabefeld.")
        if (!target.isFocused) target.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val existing = if (append && !target.isShowingHintText) target.text?.toString().orEmpty() else ""
        val args = Bundle().putCharSequenceArg(existing + text)
        if (!target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            throw PhoneException("action_failed", "Das Feld hat den Text nicht angenommen.")
        }
    }

    private fun Bundle.putCharSequenceArg(value: CharSequence): Bundle {
        putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value)
        return this
    }

    fun key(key: String) {
        val action = when (key) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            "notifications" -> GLOBAL_ACTION_NOTIFICATIONS
            else -> throw PhoneException("bad_request", "key: back, home, recents oder notifications")
        }
        if (!performGlobalAction(action)) throw PhoneException("action_failed", "Das System hat die Taste nicht ausgeführt.")
    }

    // ------------------------------------------------------------------ screenshot (Android 11+, no MediaProjection)
    suspend fun screenshot(): org.json.JSONObject {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) throw PhoneException("unsupported", "Screenshots brauchen Android 11 oder neuer.")
        val result = suspendCancellableCoroutine<Result<Bitmap>> { cont ->
            takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
                override fun onSuccess(shot: ScreenshotResult) {
                    val bmp = runCatching {
                        Bitmap.wrapHardwareBuffer(shot.hardwareBuffer, shot.colorSpace)!!.copy(Bitmap.Config.ARGB_8888, false)
                    }
                    shot.hardwareBuffer.close()
                    if (cont.isActive) cont.resume(bmp)
                }

                override fun onFailure(errorCode: Int) {
                    if (cont.isActive) cont.resume(Result.failure(PhoneException("action_failed", "Screenshot abgelehnt (Code $errorCode) – zu schnell hintereinander oder geschütztes Fenster.")))
                }
            })
        }
        val full = result.getOrElse { throw it as? PhoneException ?: PhoneException("action_failed", "Screenshot fehlgeschlagen.") }
        val scaled = if (full.width > 1080) Bitmap.createScaledBitmap(full, 1080, full.height * 1080 / full.width, true) else full
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, 70, out)
        return org.json.JSONObject()
            .put("image_base64", Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP))
            .put("width", scaled.width).put("height", scaled.height)
    }
}
