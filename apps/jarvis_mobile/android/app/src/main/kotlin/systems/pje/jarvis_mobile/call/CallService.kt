package systems.pje.jarvis_mobile.call

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.IBinder
import android.telecom.DisconnectCause
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.telecom.CallAttributesCompat
import androidx.core.telecom.CallControlResult
import androidx.core.telecom.CallControlScope
import androidx.core.telecom.CallEndpointCompat
import androidx.core.telecom.CallsManager
import io.livekit.android.LiveKit
import io.livekit.android.LiveKitOverrides
import io.livekit.android.AudioOptions
import io.livekit.android.audio.NoAudioHandler
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import io.livekit.android.room.track.RemoteAudioTrack
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import livekit.org.webrtc.AudioTrackSink
import org.json.JSONObject
import systems.pje.jarvis_mobile.assist.WakeWord
import systems.pje.jarvis_mobile.bridge.NativeEvents
import systems.pje.jarvis_mobile.net.Gateway
import systems.pje.jarvis_mobile.net.Http
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

private const val TAG = "JarvisCall"

/**
 * One Jarvis call, registered with Android Telecom through Jetpack Core-Telecom (`CallsManager`):
 * system ringing + CallStyle notification, lock screen, Bluetooth/car/watch controls (remote surfaces call
 * the `onAnswer`/`onDisconnect`/`onSetActive`/`onSetInactive` callbacks), system audio routing
 * (earpiece/speaker/Bluetooth via `requestEndpointChange`), hold during a cellular call.
 * Audio runs natively through the LiveKit Android SDK with `NoAudioHandler` – Telecom owns audio focus and
 * routing, LiveKit only captures/plays (VOICE_COMMUNICATION, echo cancellation + noise suppression on).
 */
class CallService : Service() {
    companion object {
        const val ACTION_INCOMING = "systems.pje.jarvis.call.INCOMING"
        const val ACTION_OUTGOING = "systems.pje.jarvis.call.OUTGOING"
        const val ACTION_ANSWER = "systems.pje.jarvis.call.ANSWER"
        const val ACTION_DECLINE = "systems.pje.jarvis.call.DECLINE"
        const val ACTION_HANGUP = "systems.pje.jarvis.call.HANGUP"
        const val ACTION_CANCEL = "systems.pje.jarvis.call.CANCEL"
        const val ACTION_MUTE = "systems.pje.jarvis.call.MUTE"
        const val ACTION_ENDPOINT = "systems.pje.jarvis.call.ENDPOINT"
        const val EXTRA_CALL_ID = "call_id"
        const val EXTRA_SUBJECT = "subject"
        const val EXTRA_RING_TIMEOUT = "ring_timeout"
        const val EXTRA_REASON = "reason"
    }

    private sealed interface Cmd {
        data object Answer : Cmd
        data object RemoteAnswered : Cmd
        data class Disconnect(val cause: Int, val notifyServer: String?) : Cmd
        data class Endpoint(val id: String) : Cmd
        data object Activate : Cmd
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var callsManager: CallsManager
    private lateinit var notifications: CallNotifications
    private var commands = Channel<Cmd>(Channel.UNLIMITED)
    private var callJob: Job? = null
    private var room: Room? = null
    private var endpoints: List<CallEndpointCompat> = emptyList()
    private var levelJob: Job? = null
    @Volatile private var remoteLevel = 0f
    @Volatile private var localLevel = 0f

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        notifications = CallNotifications(this)
        callsManager = CallsManager(this).apply { registerAppWithTelecom(CallsManager.CAPABILITY_BASELINE) }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: return START_NOT_STICKY
        when (action) {
            ACTION_INCOMING -> register(
                incoming = true,
                callId = intent.getStringExtra(EXTRA_CALL_ID)!!,
                subject = intent.getStringExtra(EXTRA_SUBJECT).orEmpty(),
                ringTimeout = intent.getIntExtra(EXTRA_RING_TIMEOUT, 45),
            )
            ACTION_OUTGOING -> register(incoming = false, callId = null, subject = "", ringTimeout = 0)
            ACTION_ANSWER -> commands.trySend(Cmd.Answer)
            ACTION_DECLINE -> commands.trySend(Cmd.Disconnect(DisconnectCause.REJECTED, "decline"))
            ACTION_HANGUP -> commands.trySend(Cmd.Disconnect(DisconnectCause.LOCAL, "hangup"))
            ACTION_CANCEL -> {
                val reason = intent.getStringExtra(EXTRA_REASON)
                val cause = if (reason == "answered_elsewhere") DisconnectCause.ANSWERED_ELSEWHERE else DisconnectCause.MISSED
                commands.trySend(Cmd.Disconnect(cause, null))
            }
            ACTION_MUTE -> scope.launch { setMuted(intent.getBooleanExtra("muted", false)) }
            ACTION_ENDPOINT -> commands.trySend(Cmd.Endpoint(intent.getStringExtra("endpoint").orEmpty()))
        }
        if (callJob == null && action != ACTION_INCOMING && action != ACTION_OUTGOING) stopSelf(startId)
        return START_NOT_STICKY
    }

    // ------------------------------------------------------------------ registration with Telecom
    private fun register(incoming: Boolean, callId: String?, subject: String, ringTimeout: Int) {
        if (callJob != null) return
        commands = Channel(Channel.UNLIMITED)
        CallManager.reset(
            CallState(callId = callId, direction = if (incoming) "to_owner" else "to_jarvis",
                status = if (incoming) "ringing" else "dialing", subject = subject),
        )
        // Telecom requires the call notification within 5 s of adding the call: promote immediately.
        promote(foregroundTypes(withMic = !incoming))
        val attributes = CallAttributesCompat(
            displayName = "Jarvis",
            address = Uri.parse("jarvis:core"),
            direction = if (incoming) CallAttributesCompat.DIRECTION_INCOMING else CallAttributesCompat.DIRECTION_OUTGOING,
            callType = CallAttributesCompat.CALL_TYPE_AUDIO_CALL,
            callCapabilities = CallAttributesCompat.SUPPORTS_SET_INACTIVE,
        )
        callJob = scope.launch {
            try {
                callsManager.addCall(
                    attributes,
                    onAnswer = { _ -> onRemoteAnswer() },              // Bluetooth headset, car, watch
                    onDisconnect = { cause -> onRemoteDisconnect(cause) },
                    onSetActive = { onRemoteActive() },
                    onSetInactive = { onRemoteHold() },
                ) {
                    val call = this
                    launch {
                        try {
                            runCall(call, incoming, ringTimeout)
                        } finally {
                            // addCall keeps suspending after the disconnect – end the session explicitly
                            scope.launch { delay(300); callJob?.cancel() }
                        }
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Telecom call failed", e)
                CallManager.update { it.copy(status = "ended", error = e.message ?: "Telecom-Fehler") }
            } finally {
                teardown()
            }
        }
    }

    private suspend fun runCall(call: CallControlScope, incoming: Boolean, ringTimeout: Int) {
        call.launch { call.availableEndpoints.collect { list -> endpoints = list; publishEndpoints() } }
        call.launch { call.currentCallEndpoint.collect { publishEndpoints(it) } }
        call.launch { call.isMuted.collect { m -> if (m != CallManager.state.value.muted) setMuted(m) } }
        if (incoming) {
            call.launch { // local ring timeout as a safety net (the core sends call_cancel too)
                delay((ringTimeout + 5) * 1000L)
                if (CallManager.state.value.status == "ringing") commands.trySend(Cmd.Disconnect(DisconnectCause.MISSED, null))
            }
        } else {
            call.launch { connect(call, incoming = false) }
        }
        for (cmd in commands) {
            when (cmd) {
                Cmd.Answer -> if (CallManager.state.value.status == "ringing") {
                    when (call.answer(CallAttributesCompat.CALL_TYPE_AUDIO_CALL)) {
                        is CallControlResult.Success -> call.launch { connect(call, incoming = true) }
                        is CallControlResult.Error -> commands.trySend(Cmd.Disconnect(DisconnectCause.ERROR, "decline"))
                    }
                }
                Cmd.RemoteAnswered -> call.launch { connect(call, incoming = true) } // Telecom already answered
                Cmd.Activate -> call.setActive()
                is Cmd.Endpoint -> endpoints.firstOrNull { it.identifier.toString() == cmd.id }?.let { call.requestEndpointChange(it) }
                is Cmd.Disconnect -> {
                    val s = CallManager.state.value
                    when (cmd.notifyServer) {
                        "decline" -> if (s.status == "ringing") serverPost("decline", s.callId)
                        "hangup" -> serverPost("hangup", s.callId)
                    }
                    CallManager.update { it.copy(status = "ended", endReason = reasonName(cmd.cause)) }
                    call.disconnect(DisconnectCause(telecomCause(cmd.cause)))
                    return
                }
            }
        }
    }

    // Remote-surface callbacks must return within 5 s – the real work happens on the command loop.
    private suspend fun onRemoteAnswer() {
        CallManager.update { it.copy(status = "connecting") }
        commands.trySend(Cmd.RemoteAnswered)
    }

    private suspend fun onRemoteDisconnect(cause: DisconnectCause) {
        val s = CallManager.state.value
        if (s.status == "ringing") serverPost("decline", s.callId) else if (s.status != "ended") serverPost("hangup", s.callId)
        CallManager.update { it.copy(status = "ended", endReason = reasonName(cause.code)) }
        commands.close()
    }

    private suspend fun onRemoteActive() {
        CallManager.update { if (it.status == "on_hold") it.copy(status = "active") else it }
        room?.localParticipant?.setMicrophoneEnabled(!CallManager.state.value.muted)
    }

    private suspend fun onRemoteHold() { // e.g. a cellular call was accepted: pause our audio
        CallManager.update { it.copy(status = "on_hold") }
        room?.localParticipant?.setMicrophoneEnabled(false)
    }

    // ------------------------------------------------------------------ LiveKit
    private suspend fun connect(call: CallControlScope, incoming: Boolean) {
        val gw = Gateway.get(this)
        CallManager.update { it.copy(status = "connecting") }
        promote(foregroundTypes(withMic = true))
        val info = try {
            val options = JSONObject().put("barge_in", WakeWord.bargeIn(this)).toString() // Sir may talk over Jarvis (setting)
            val text = if (incoming) gw.request("POST", "/api/mobile/calls/${CallManager.state.value.callId}/answer", options)
            else gw.request("POST", "/api/mobile/calls", options)
            JSONObject(text)
        } catch (e: Exception) {
            Log.w(TAG, "Call setup failed: ${e.message}")
            CallManager.update { it.copy(error = e.message ?: "Verbindung fehlgeschlagen") }
            commands.trySend(Cmd.Disconnect(DisconnectCause.ERROR, null))
            return
        }
        CallManager.update { it.copy(callId = info.getString("call_id")) }
        val base = gw.resolveBase()
        val url = if (info.optBoolean("via_gateway", true)) base.replaceFirst("https://", "wss://").replaceFirst("http://", "ws://")
        else info.getString("url")
        val r = LiveKit.create(
            applicationContext,
            overrides = LiveKitOverrides(
                okHttpClient = Http.client(gw.pin),
                audioOptions = AudioOptions(audioHandler = NoAudioHandler()),
            ),
        )
        room = r
        call.launch { observeRoom(r, call) }
        try {
            withTimeout(20_000) { r.connect(url, info.getString("token")) }
            r.localParticipant.setMicrophoneEnabled(!CallManager.state.value.muted)
            r.localParticipant.getTrackPublication(io.livekit.android.room.track.Track.Source.MICROPHONE)
                ?.track?.let { (it as? io.livekit.android.room.track.LocalAudioTrack)?.addSink(levelSink { v -> localLevel = v }) }
        } catch (e: Exception) {
            Log.w(TAG, "LiveKit connect failed", e)
            CallManager.update { it.copy(error = "Audioverbindung fehlgeschlagen: ${e.message}") }
            commands.trySend(Cmd.Disconnect(DisconnectCause.ERROR, "hangup"))
            return
        }
        if (!incoming) commands.trySend(Cmd.Activate) // outgoing: Jarvis is already in the room
        CallManager.update { it.copy(status = "active", activeSince = System.currentTimeMillis()) }
        startLevels()
    }

    private suspend fun observeRoom(r: Room, call: CallControlScope) {
        r.events.collect { ev ->
            when (ev) {
                is RoomEvent.TrackSubscribed -> (ev.track as? RemoteAudioTrack)?.addSink(levelSink { v -> remoteLevel = v })
                is RoomEvent.DataReceived -> if (ev.topic == "jarvis") NativeEvents.emit("call_data", String(ev.data))
                is RoomEvent.Reconnecting -> CallManager.update { it.copy(status = "reconnecting") }
                is RoomEvent.Reconnected -> CallManager.update { it.copy(status = "active") }
                is RoomEvent.ParticipantDisconnected -> if (ev.participant.identity?.value == "jarvis") {
                    commands.trySend(Cmd.Disconnect(DisconnectCause.REMOTE, null))
                }
                is RoomEvent.Disconnected -> if (CallManager.state.value.status != "ended") {
                    commands.trySend(Cmd.Disconnect(DisconnectCause.REMOTE, null))
                }
                else -> Unit
            }
        }
    }

    private fun levelSink(set: (Float) -> Unit) = AudioTrackSink { data: ByteBuffer, bits: Int, _: Int, channels: Int, frames: Int, _: Long ->
        if (bits != 16 || frames <= 0) return@AudioTrackSink
        val buf = data.duplicate().order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        var sum = 0.0
        val n = minOf(buf.remaining(), frames * channels)
        for (i in 0 until n) { val s = buf.get(i).toDouble(); sum += s * s }
        set((sqrt(sum / maxOf(1, n)) / 6000.0).coerceIn(0.0, 1.0).toFloat())
    }

    private fun startLevels() {
        levelJob?.cancel()
        levelJob = scope.launch {
            var r = 0f; var l = 0f
            while (true) {
                r = r * 0.6f + remoteLevel * 0.4f; l = l * 0.6f + localLevel * 0.4f
                NativeEvents.emit("call_level", "{\"out\":$r,\"in\":$l}")
                delay(33)
            }
        }
    }

    private suspend fun setMuted(muted: Boolean) {
        CallManager.update { it.copy(muted = muted) }
        room?.localParticipant?.setMicrophoneEnabled(!muted)
    }

    private fun publishEndpoints(current: CallEndpointCompat? = null) {
        CallManager.update { s ->
            s.copy(
                endpoints = endpoints.map { Endpoint(it.identifier.toString(), it.name.toString(), it.type) },
                endpoint = current?.let { Endpoint(it.identifier.toString(), it.name.toString(), it.type) } ?: s.endpoint,
            )
        }
    }

    // ------------------------------------------------------------------ helpers
    private fun foregroundTypes(withMic: Boolean): Int {
        var t = ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL
        if (withMic && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) t = t or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        return t
    }

    private fun promote(types: Int) {
        val n = notifications.build(CallManager.state.value)
        try {
            ServiceCompat.startForeground(this, CallNotifications.ID, n, types)
        } catch (e: Exception) { // microphone type not allowed from the background → Telecom delegation covers it
            Log.w(TAG, "startForeground($types) failed: ${e.message}; retrying as phoneCall")
            ServiceCompat.startForeground(this, CallNotifications.ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL)
        }
        if (notifyJob == null) notifyJob = scope.launch { CallManager.state.collect { notifications.update(it) } }
    }
    private var notifyJob: Job? = null

    private fun serverPost(action: String, callId: String?) {
        if (callId == null) return
        CoroutineScope(Dispatchers.IO).launch {
            runCatching { Gateway.get(this@CallService).request("POST", "/api/mobile/calls/$callId/$action") }
                .onFailure { Log.w(TAG, "$action failed: ${it.message}") }
        }
    }

    /** Core-Telecom only accepts LOCAL, REMOTE, MISSED and REJECTED. */
    private fun telecomCause(code: Int) = when (code) {
        DisconnectCause.REJECTED, DisconnectCause.MISSED, DisconnectCause.REMOTE -> code
        DisconnectCause.ANSWERED_ELSEWHERE -> DisconnectCause.REMOTE
        else -> DisconnectCause.LOCAL
    }

    private fun reasonName(code: Int) = when (code) {
        DisconnectCause.REJECTED -> "declined"
        DisconnectCause.MISSED -> "missed"
        DisconnectCause.LOCAL -> "hangup"
        DisconnectCause.REMOTE -> "remote"
        DisconnectCause.ANSWERED_ELSEWHERE -> "answered_elsewhere"
        DisconnectCause.ERROR -> "error"
        else -> "ended"
    }

    private fun teardown() {
        levelJob?.cancel()
        room?.disconnect(); room?.release(); room = null
        callJob = null
        notifyJob?.cancel(); notifyJob = null
        NativeEvents.emit("call_level", "{\"out\":0,\"in\":0}")
        CallManager.update { if (it.status != "ended") it.copy(status = "ended") else it }
        notifications.cancel()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
