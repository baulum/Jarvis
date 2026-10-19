package systems.pje.jarvis_mobile.assist

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import systems.pje.jarvis_mobile.call.CallManager
import systems.pje.jarvis_mobile.net.ApiException
import systems.pje.jarvis_mobile.net.Gateway

enum class Phase { IDLE, LISTENING, TRANSCRIBING, THINKING, SPEAKING, ERROR }

data class AssistUi(
    val phase: Phase = Phase.IDLE,
    val status: String = "",
    val heard: String = "",
    val answer: String = "",
    val level: Float = 0f,
)

/** The two core calls of a voice turn – both on the mobile gateway allowlist (`/api/voice/stt`, `/api/chat`). */
object JarvisVoiceApi {
    data class Reply(val sessionId: String?, val text: String, val error: String?)

    suspend fun transcribe(c: Context, wav: ByteArray): String =
        JSONObject(Gateway.get(c).upload("/api/voice/stt", wav, "audio/wav", readTimeoutS = 60)).optString("text").trim()

    suspend fun ask(c: Context, text: String, sessionId: String?): Reply {
        val body = JSONObject().put("message", text).put("session_id", sessionId).put("channel", "mobile").put("wait", true)
        val j = JSONObject(Gateway.get(c).request("POST", "/api/chat", body.toString(), readTimeoutS = 180))
        fun str(key: String) = if (j.isNull(key)) null else j.optString(key).ifBlank { null }
        return Reply(str("session_id"), str("text").orEmpty(), str("error"))
    }
}

/**
 * One overlay conversation: listen → Whisper on the core → same agent/session as the app → show + speak the answer.
 * If Jarvis asks something back, it listens again right away (like the Mac's follow-up window).
 */
class AssistSession(context: Context, private val onUi: (AssistUi) -> Unit) {
    companion object {
        private const val SESSION_TTL_MS = 10 * 60 * 1000L
        @Volatile private var lastSession: Pair<String, Long>? = null
    }

    private val app = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val speech = Speech(app)
    private val focus = AudioFocus(app)
    private var job: Job? = null
    @Volatile private var stopRequested = false

    var ui = AssistUi()
        private set(v) {
            field = v; onUi(v)
        }

    /** Chat session of this conversation (for "open in app"). */
    val sessionId: String? get() = lastSession?.takeIf { System.currentTimeMillis() - it.second < SESSION_TTL_MS }?.first

    fun listen() {
        job?.cancel()
        speech.stop()
        Assist.open.value = true
        val turn = scope.launch(start = CoroutineStart.LAZY) {
            try {
                converse()
            } finally {
                if (job === coroutineContext[Job]) focus.release() // a newer turn may already hold the focus
            }
        }
        job = turn
        turn.start()
    }

    /** Ends the current utterance now (tap while listening). */
    fun finishSpeaking() {
        stopRequested = true
    }

    fun message(text: String) {
        ui = AssistUi(Phase.IDLE, text)
    }

    /** Overlay hidden: stop everything and give the microphone back to the wake word. */
    fun stop() {
        job?.cancel(); job = null
        speech.stop(); focus.release()
        Assist.open.value = false
    }

    fun release() {
        stop()
        scope.cancel()
        speech.shutdown()
    }

    private suspend fun converse() {
        when {
            !Gateway.get(app).paired -> return fail("Die App ist noch nicht mit Jarvis gekoppelt.")
            CallManager.busy -> return fail("Gerade läuft ein Gespräch mit Jarvis.")
            !WakeWord.hasMic(app) -> return fail("Mikrofonberechtigung fehlt – bitte in der App unter Einstellungen › „Hey Jarvis“ erlauben.")
        }
        var followUp = false
        while (true) {
            focus.acquire()
            ui = AssistUi(Phase.LISTENING, if (followUp) "Ich höre, Sir …" else "Ja, Sir?", answer = if (followUp) ui.answer else "")
            if (!followUp) {
                Earcon.listening(); delay(200) // keep the tone out of the recording
            }
            stopRequested = false
            val wav = try {
                withContext(Dispatchers.IO) {
                    VoiceRecorder.record(
                        noSpeechMs = if (followUp) 5_000 else 7_000,
                        active = { isActive },
                        stopEarly = { stopRequested },
                        onLevel = { l -> scope.launch { if (ui.phase == Phase.LISTENING) ui = ui.copy(level = l) } },
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return fail(e.message ?: "Mikrofon nicht verfügbar")
            }
            if (wav == null) {
                ui = if (followUp) ui.copy(phase = Phase.IDLE, status = "", level = 0f) else AssistUi(Phase.IDLE, "Ich habe nichts gehört, Sir.")
                return
            }
            ui = ui.copy(phase = Phase.TRANSCRIBING, status = "Verstehe …", heard = "", answer = "", level = 0f)
            val text = try {
                JarvisVoiceApi.transcribe(app, wav)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return fail(describe(e))
            }
            if (text.isBlank()) {
                ui = ui.copy(phase = Phase.IDLE, status = "Das habe ich nicht verstanden, Sir."); return
            }
            ui = ui.copy(phase = Phase.THINKING, status = "Einen Moment …", heard = text)
            val slow = scope.launch {
                delay(8_000)
                if (ui.phase == Phase.THINKING) ui = ui.copy(status = "Jarvis arbeitet daran …")
            }
            val reply = try {
                JarvisVoiceApi.ask(app, text, sessionId)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return fail(describe(e))
            } finally {
                slow.cancel()
            }
            reply.sessionId?.let { lastSession = it to System.currentTimeMillis() }
            val answer = reply.text.ifBlank { reply.error?.let { "Das hat nicht geklappt: $it" } ?: "Erledigt, Sir." }
            ui = ui.copy(phase = Phase.SPEAKING, status = "", answer = answer)
            speech.speak(answer)
            followUp = answer.trimEnd().endsWith("?")
            if (!followUp) {
                ui = ui.copy(phase = Phase.IDLE); return
            }
        }
    }

    private fun fail(message: String) {
        ui = ui.copy(phase = Phase.ERROR, status = message, level = 0f)
    }

    private fun describe(e: Exception): String = when (e) {
        is ApiException -> e.message ?: "Fehler"
        else -> "Jarvis ist nicht erreichbar"
    }
}
