package systems.pje.jarvis_mobile.bridge

import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject
import systems.pje.jarvis_mobile.JarvisApp
import systems.pje.jarvis_mobile.assist.Assist
import systems.pje.jarvis_mobile.assist.WakeWord
import systems.pje.jarvis_mobile.call.CallManager
import systems.pje.jarvis_mobile.net.Gateway
import systems.pje.jarvis_mobile.net.randomNonce
import systems.pje.jarvis_mobile.net.sha256Hex
import systems.pje.jarvis_mobile.push.PushRegistration
import systems.pje.jarvis_mobile.secure.DeviceKey

/** Native → Flutter events (JSON strings). Buffered while no Flutter listener is attached. */
object NativeEvents {
    private val main = Handler(Looper.getMainLooper())
    private var sink: EventChannel.EventSink? = null
    private val pending = ArrayDeque<String>()

    fun emit(type: String, data: String) {
        val payload = "{\"type\":\"$type\",\"data\":$data}"
        main.post {
            val s = sink
            if (s != null) s.success(payload)
            else if (type != "call_level") { pending.addLast(payload); while (pending.size > 50) pending.removeFirst() }
        }
    }

    fun attach(s: EventChannel.EventSink?) {
        sink = s
        while (s != null && pending.isNotEmpty()) s.success(pending.removeFirst())
    }
}

class NativeBridge(private val context: Context, messenger: BinaryMessenger) : MethodChannel.MethodCallHandler {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val gw = Gateway.get(context)

    init {
        MethodChannel(messenger, "jarvis/native").setMethodCallHandler(this)
        EventChannel(messenger, "jarvis/events").setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) = NativeEvents.attach(events)
            override fun onCancel(arguments: Any?) = NativeEvents.attach(null)
        })
        gw.onRevoked = { NativeEvents.emit("revoked", "{}") }
        scope.launch { CallManager.state.collect { NativeEvents.emit("call_state", it.toJson()) } }
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        fun async(block: suspend () -> Any?) = scope.launch {
            try { result.success(block()) } catch (e: Exception) { result.error("jarvis", e.message ?: e.toString(), null) }
        }
        when (call.method) {
            "status" -> result.success(status())
            "pair" -> async {
                val res = gw.pair(call.argument<String>("payload")!!, call.argument<String>("name")!!, PushRegistration.appVersion(context))
                res.toString()
            }
            "accessToken" -> async { gw.accessToken() }
            "baseUrl" -> async { gw.resolveBase(force = call.argument<Boolean>("force") == true) }
            "logout" -> async { gw.logout(); true }
            "forget" -> { gw.forget(); result.success(true) }
            "configurePush" -> async { (context.applicationContext as JarvisApp).configureFirebase(call.argument<String>("options")) }
            "sign" -> {
                val purpose = call.argument<String>("purpose")!!
                val body = call.argument<String>("body") ?: ""
                val ts = System.currentTimeMillis() / 1000
                val nonce = randomNonce()
                val sig = DeviceKey.sign("$purpose|${gw.deviceId}|$ts|$nonce|${sha256Hex(body.toByteArray())}".toByteArray())
                result.success(mapOf("ts" to ts.toString(), "nonce" to nonce, "signature" to sig))
            }
            "callState" -> result.success(CallManager.state.value.toJson())
            "startCall" -> { CallManager.startOutgoing(context); result.success(true) }
            "answer" -> { CallManager.answer(context); result.success(true) }
            "decline" -> { CallManager.decline(context); result.success(true) }
            "hangup" -> { CallManager.hangup(context); result.success(true) }
            "setMuted" -> { CallManager.setMuted(context, call.argument<Boolean>("muted") == true); result.success(true) }
            "setEndpoint" -> { CallManager.setEndpoint(context, call.argument<String>("id")!!); result.success(true) }
            "openSettings" -> { openSettings(call.argument<String>("kind") ?: "app"); result.success(true) }
            "wakeStatus" -> result.success(WakeWord.status(context))
            "wakeConfigure" -> {
                WakeWord.configure(context, call.argument<Boolean>("enabled"), call.argument<Boolean>("screenOnly"), call.argument<String>("accessKey"))
                result.success(WakeWord.status(context))
            }
            "openAssist" -> { Assist.launch(context, "app"); result.success(true) }
            else -> result.notImplemented()
        }
    }

    private fun status(): String {
        val nm = context.getSystemService(NotificationManager::class.java)
        return JSONObject()
            .put("paired", gw.paired)
            .put("device_id", gw.deviceId)
            .put("base_url", gw.baseUrl)
            .put("pin", gw.pin)
            .put("urls", org.json.JSONArray(gw.urls()))
            .put("notifications_enabled", nm.areNotificationsEnabled())
            .put("full_screen_allowed", if (Build.VERSION.SDK_INT >= 34) nm.canUseFullScreenIntent() else true)
            .put("push_ready", (context.applicationContext as JarvisApp).firebaseReady)
            .put("push_token_uploaded", gw.store.get("fcm_token_uploaded") != null)
            .put("model", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("sdk", Build.VERSION.SDK_INT)
            .toString()
    }

    private fun openSettings(kind: String) {
        val pkg = Uri.parse("package:${context.packageName}")
        val intent = when (kind) {
            "notifications" -> Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
            "fullscreen" -> if (Build.VERSION.SDK_INT >= 34) Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, pkg) else Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg)
            "battery" -> Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            "overlay" -> Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, pkg)
            // "Digital assistant app" page on Pixel/Samsung; elsewhere the list of default apps
            "assistant" -> Intent(Settings.ACTION_VOICE_INPUT_SETTINGS)
            else -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg)
        }
        try {
            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            val fallback = if (kind == "assistant") Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS) else Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg)
            context.startActivity(fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }
}
