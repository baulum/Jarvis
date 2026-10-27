package systems.pje.jarvis_mobile.push

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import org.json.JSONObject
import systems.pje.jarvis_mobile.bridge.NativeEvents
import systems.pje.jarvis_mobile.call.CallManager
import systems.pje.jarvis_mobile.notify.AlertNotifier
import systems.pje.jarvis_mobile.update.AppUpdater

/**
 * Device test hook without Firebase: delivers a decrypted push payload into exactly the code path FCM uses.
 * Protected by android.permission.DUMP – only the system and the adb shell can send it, never another app.
 * A fake call is still useless: answering needs a call that is ringing on the core.
 *
 *   adb shell am broadcast -a systems.pje.jarvis.DEV_PUSH -n <pkg>/systems.pje.jarvis_mobile.push.DevPushReceiver \
 *       --es payload '{"type":"call","call_id":"…","subject":"…","ring_timeout_s":45}'
 */
class DevPushReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val msg = runCatching { JSONObject(intent.getStringExtra("payload") ?: return) }.getOrElse { return }
        Log.i("JarvisPush", "DEV push (adb): ${msg.optString("type")}")
        when (msg.optString("type")) {
            "call" -> CallManager.onIncomingPush(context, msg)
            "call_cancel" -> CallManager.onCancelPush(context, msg.optString("call_id"), msg.optString("status"))
            "alert" -> { AlertNotifier.show(context, msg); NativeEvents.emit("alert", msg.toString()) }
            "app_update" -> AppUpdater.onPush(context)
        }
    }
}
