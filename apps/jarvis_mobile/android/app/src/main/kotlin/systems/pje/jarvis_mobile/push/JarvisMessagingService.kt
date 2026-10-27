package systems.pje.jarvis_mobile.push

import android.content.Context
import android.util.Base64
import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject
import systems.pje.jarvis_mobile.bridge.NativeEvents
import systems.pje.jarvis_mobile.call.CallManager
import systems.pje.jarvis_mobile.location.LocationShare
import systems.pje.jarvis_mobile.net.Gateway
import systems.pje.jarvis_mobile.notify.AlertNotifier
import systems.pje.jarvis_mobile.phone.PhoneControl
import systems.pje.jarvis_mobile.secure.PushKey
import systems.pje.jarvis_mobile.update.AppUpdater

private const val TAG = "JarvisPush"

/**
 * Decrypts and validates push payloads: AES-GCM with the paired device key (AAD binds it to this device),
 * expiry check and duplicate suppression by message id (replay protection).
 */
object PushCrypto {
    private const val PREFS = "jarvis_push_seen"
    private const val MAX_SEEN = 300

    fun open(context: Context, data: Map<String, String>): JSONObject? {
        val device = Gateway.get(context).deviceId ?: return null
        if (data["v"] != "1" || !PushKey.exists()) return null
        val plain = runCatching {
            PushKey.decrypt(
                Base64.decode(data["n"], Base64.NO_WRAP),
                Base64.decode(data["c"], Base64.NO_WRAP),
                "jarvis-push|$device".toByteArray(),
            )
        }.getOrElse { Log.w(TAG, "Dropping push that failed authentication"); return null }
        val msg = JSONObject(String(plain))
        val now = System.currentTimeMillis() / 1000
        if (msg.optLong("exp", 0) < now) {
            Log.i(TAG, "Dropping expired push ${msg.optString("type")}"); return null
        }
        if (!firstSeen(context, msg.optString("id"))) {
            Log.i(TAG, "Dropping duplicate push"); return null
        }
        return msg
    }

    @Synchronized
    private fun firstSeen(context: Context, id: String): Boolean {
        if (id.length < 16) return false
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val seen = prefs.getString("ids", "")!!.split(",").filter { it.isNotBlank() }.toMutableList()
        if (id in seen) return false
        seen.add(id)
        prefs.edit().putString("ids", seen.takeLast(MAX_SEEN).joinToString(",")).apply()
        return true
    }
}

class JarvisMessagingService : FirebaseMessagingService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onMessageReceived(message: RemoteMessage) {
        val msg = PushCrypto.open(this, message.data) ?: return
        when (msg.optString("type")) {
            "call" -> CallManager.onIncomingPush(this, msg)
            "call_cancel" -> CallManager.onCancelPush(this, msg.optString("call_id"), msg.optString("status"))
            "alert" -> {
                AlertNotifier.show(this, msg)
                NativeEvents.emit("alert", msg.toString())
            }
            // Jarvis needs a current position; ignored unless Sir enabled location sharing in the app
            "location_request" -> scope.launch { LocationShare.report(this@JarvisMessagingService) }
            // Jarvis operates the phone (phone_* tools); executed by the accessibility service, answer goes over HTTP
            "phone_command" -> scope.launch { PhoneControl.handle(this@JarvisMessagingService, msg) }
            // a new signed release of this app is ready on the core: check, download, install (update/AppUpdater)
            "app_update" -> AppUpdater.onPush(this)
            else -> Log.i(TAG, "Unknown push type ${msg.optString("type")}")
        }
    }

    override fun onNewToken(token: String) {
        val gw = Gateway.get(this)
        gw.store.put("fcm_token", token)
        if (!gw.paired) return
        scope.launch { PushRegistration.upload(this@JarvisMessagingService, token) }
    }
}

object PushRegistration {
    suspend fun upload(context: Context, token: String): Boolean {
        val gw = Gateway.get(context)
        return runCatching {
            gw.request("PUT", "/api/mobile/push-token", JSONObject().put("token", token).put("app_version", appVersion(context)).toString())
            gw.store.put("fcm_token_uploaded", token)
            true
        }.getOrElse { Log.w(TAG, "Push token upload failed: ${it.message}"); false }
    }

    fun appVersion(context: Context): String =
        runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull() ?: "?"
}
