package systems.pje.jarvis_mobile.assist

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.selects.select
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

/** The core calls of a voice turn – all on the mobile gateway allowlist (`/api/voice/stt`, `/api/chat`, `/api/chat/interrupt`). */
object JarvisVoiceApi {
    data class Reply(val sessionId: String?, val text: String, val error: String?)

    /** [stop]: the core recognised a bare stop command ("Stopp", "Jarvis, stopp") – it is not sent to the agent. */
    data class Heard(val text: String, val stop: Boolean)

    suspend fun transcribe(c: Context, wav: ByteArray): Heard {
        val j = JSONObject(Gateway.get(c).upload("/api/voice/stt", wav, "audio/wav", readTimeoutS = 60))
        return Heard(j.optString("text").trim(), j.optString("command") == "stop")
    }

    /** Drops the answer the core is still writing (best effort: the overlay has already stopped listening to it). */
    suspend fun interruptRun(c: Context) {
        runCatching { Gateway.get(c).request("POST", "/api/chat/interrupt") }
    }

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
        dropRunningAnswer()
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

    /** Stopp button: Jarvis falls silent at once and the answer he is still writing is dropped. The panel stays open. */
    fun interrupt() {
        dropRunningAnswer()
        focus.release()
        ui = ui.copy(phase = Phase.IDLE, status = "Gestoppt, Sir.", level = 0f)
    }

    /** Stops speaking and the turn in progress; while the core is still working on it, its run is cancelled too. */
    private fun dropRunningAnswer() {
        val working = ui.phase == Phase.TRANSCRIBING || ui.phase == Phase.THINKING
        job?.cancel(); job = null
        speech.stop()
        if (working) CoroutineScope(Dispatchers.IO).launch { JarvisVoiceApi.interruptRun(app) }
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
        var interrupting: BargeInListener.Heard? = null // Sir talked over Jarvis: his words so far
        var lastAnswer = ""
        while (true) {
            focus.acquire()
            val lead = interrupting
            interrupting = null
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
                        lead = lead?.pcm,
                        noiseHint = lead?.noise ?: 0.0,
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
            val heard = try {
                JarvisVoiceApi.transcribe(app, wav)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return fail(describe(e))
            }
            val text = heard.text
            if (text.isBlank()) {
                ui = ui.copy(phase = Phase.IDLE, status = "Das habe ich nicht verstanden, Sir."); return
            }
            if (heard.stop) { // "Stopp": nothing to say, nothing for the agent
                ui = ui.copy(phase = Phase.IDLE, status = "Gestoppt, Sir.", heard = text, level = 0f); return
            }
            if (lead != null && isEcho(text, lastAnswer)) { // his own voice from the speaker, not Sir: carry on listening
                followUp = true; continue
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
            lastAnswer = answer
            interrupting = speakUntilInterrupted(answer)
            followUp = interrupting != null || answer.trimEnd().endsWith("?")
            if (!followUp) {
                ui = ui.copy(phase = Phase.IDLE); return
            }
        }
    }

    /**
     * Reads the answer out while listening for Sir. If he talks over it the speech stops at once and what he said
     * so far is returned (it becomes the start of his next utterance); null if the answer was read to the end.
     * Without a microphone permission or while the microphone is busy the answer is simply read out.
     */
    private suspend fun speakUntilInterrupted(text: String): BargeInListener.Heard? = coroutineScope {
        val speaking = async { speech.speak(text) }
        val watcher = async(Dispatchers.IO) {
            if (WakeWord.hasMic(app)) BargeInListener.listen { isActive } ?: awaitCancellation() else awaitCancellation()
        }
        val heard = select<BargeInListener.Heard?> {
            watcher.onAwait { it }
            speaking.onAwait { null }
        }
        watcher.cancel()
        if (heard != null) speech.stop()
        speaking.cancel()
        heard
    }

    /** The recognised text is (part of) what Jarvis just said: the speaker's sound picked up again, not Sir. */
    private fun isEcho(heard: String, said: String): Boolean {
        fun words(s: String) = s.lowercase().replace(Regex("[^\\p{L}\\p{N}\\s]"), " ").split(Regex("\\s+")).filter { it.isNotEmpty() }
        val h = words(heard)
        if (h.size < 4) return false
        return words(said).joinToString(" ").contains(h.joinToString(" "))
    }

    private fun fail(message: String) {
        ui = ui.copy(phase = Phase.ERROR, status = message, level = 0f)
    }

    private fun describe(e: Exception): String = when (e) {
        is ApiException -> e.message ?: "Fehler"
        else -> "Jarvis ist nicht erreichbar"
    }
}
