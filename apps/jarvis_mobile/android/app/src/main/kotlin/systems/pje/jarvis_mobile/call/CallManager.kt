package systems.pje.jarvis_mobile.call

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject

data class Endpoint(val id: String, val name: String, val type: Int)

/**
 * Observable state of the (single) Jarvis call. Written by [CallService], read by the Flutter bridge,
 * the car screen and the notifications.
 */
data class CallState(
    val callId: String? = null,
    val direction: String = "",          // to_owner (Jarvis rings) | to_jarvis (Sir calls)
    val status: String = "idle",          // idle | ringing | dialing | connecting | active | on_hold | reconnecting | ended
    val subject: String = "",
    val muted: Boolean = false,
    val endpoint: Endpoint? = null,
    val endpoints: List<Endpoint> = emptyList(),
    val activeSince: Long? = null,
    val endReason: String? = null,
    val error: String? = null,
) {
    fun toJson(): String = JSONObject()
        .put("call_id", callId).put("direction", direction).put("status", status).put("subject", subject)
        .put("muted", muted).put("endpoint", endpoint?.let { ep(it) })
        .put("endpoints", JSONArray(endpoints.map { ep(it) }))
        .put("active_since", activeSince).put("end_reason", endReason).put("error", error)
        .toString()

    private fun ep(e: Endpoint) = JSONObject().put("id", e.id).put("name", e.name).put("type", e.type)
}

object CallManager {
    private val _state = MutableStateFlow(CallState())
    val state: StateFlow<CallState> = _state.asStateFlow()

    internal fun update(transform: (CallState) -> CallState) {
        _state.value = transform(_state.value)
    }

    internal fun reset(final: CallState) {
        _state.value = final
    }

    val busy: Boolean get() = _state.value.status !in listOf("idle", "ended")

    // ------------------------------------------------------------------ entry points
    fun onIncomingPush(context: Context, msg: JSONObject) {
        val callId = msg.optString("call_id")
        if (callId.isBlank()) return
        if (busy) return // one call at a time; the core ends the other one as missed
        start(context, CallService.ACTION_INCOMING) {
            putExtra(CallService.EXTRA_CALL_ID, callId)
            putExtra(CallService.EXTRA_SUBJECT, msg.optString("subject"))
            putExtra(CallService.EXTRA_RING_TIMEOUT, msg.optInt("ring_timeout_s", 45))
        }
    }

    fun onCancelPush(context: Context, callId: String, status: String) {
        val s = _state.value
        if (s.callId != callId || s.status != "ringing") return // already answered here → ignore
        start(context, CallService.ACTION_CANCEL) {
            putExtra(CallService.EXTRA_CALL_ID, callId); putExtra(CallService.EXTRA_REASON, status)
        }
    }

    fun startOutgoing(context: Context) = start(context, CallService.ACTION_OUTGOING) {}
    fun answer(context: Context) = start(context, CallService.ACTION_ANSWER) {}
    fun decline(context: Context) = start(context, CallService.ACTION_DECLINE) {}
    fun hangup(context: Context) = start(context, CallService.ACTION_HANGUP) {}
    fun setMuted(context: Context, muted: Boolean) = start(context, CallService.ACTION_MUTE) { putExtra("muted", muted) }
    fun setEndpoint(context: Context, id: String) = start(context, CallService.ACTION_ENDPOINT) { putExtra("endpoint", id) }

    private fun start(context: Context, action: String, extras: Intent.() -> Unit) {
        val intent = Intent(context, CallService::class.java).setAction(action).apply(extras)
        if (action == CallService.ACTION_INCOMING || action == CallService.ACTION_OUTGOING) {
            ContextCompat.startForegroundService(context, intent)
        } else {
            context.startService(intent)
        }
    }
}
