package systems.pje.jarvis_mobile.assist

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.ToneGenerator
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale
import kotlin.coroutines.resume
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/** One spoken utterance from the microphone, cut by a simple adaptive energy endpointer. 16 kHz mono WAV. */
object VoiceRecorder {
    private const val RATE = 16_000
    private const val FRAME = RATE / 50 // 20 ms
    private const val PREROLL_FRAMES = 15 // 300 ms before speech onset
    private const val CALIBRATION_FRAMES = 10
    private const val SILENCE_MS = 1_100
    private const val MAX_MS = 20_000

    /**
     * Blocks until Sir stops speaking. Returns null if nothing was said within [noSpeechMs] or [active] turned
     * false; [stopEarly] ends the utterance like a pause would. [lead] is speech that was already heard (the words
     * that interrupted Jarvis, see [BargeInListener]): it becomes the start of the utterance, [noiseHint] replaces
     * the calibration that would otherwise mistake that speech for room noise.
     */
    @SuppressLint("MissingPermission") // callers check RECORD_AUDIO
    fun record(
        noSpeechMs: Int,
        active: () -> Boolean,
        stopEarly: () -> Boolean = { false },
        onSpeech: () -> Unit = {},
        onLevel: (Float) -> Unit = {},
        lead: ByteArray? = null,
        noiseHint: Double = 0.0,
    ): ByteArray? {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(min, FRAME * 2 * 10))
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release(); throw IllegalStateException("Mikrofon ist belegt oder nicht verfügbar")
        }
        val pcm = ByteArrayOutputStream()
        val preroll = ArrayDeque<ShortArray>()
        val buf = ShortArray(FRAME)
        var frames = if (lead != null) CALIBRATION_FRAMES else 0 // a lead proves speech: nothing to calibrate
        var noise = if (lead != null) noiseHint else 0.0
        var loudRun = 0
        var started = false
        var silenceMs = 0
        try {
            if (lead != null) {
                pcm.write(lead); started = true; onSpeech()
            }
            rec.startRecording()
            while (active()) {
                val n = rec.read(buf, 0, FRAME)
                if (n < 0) throw IllegalStateException("Mikrofon-Lesefehler ($n)")
                if (n == 0) continue
                frames++
                val rms = rms(buf, n)
                onLevel(min(1f, (rms / 3500.0).toFloat()))
                if (frames <= CALIBRATION_FRAMES) noise = if (frames == 1) rms else noise * 0.7 + rms * 0.3
                val threshold = max(noise * 2.8, 400.0)
                val chunk = buf.copyOf(n)
                if (!started) {
                    preroll.addLast(chunk)
                    if (preroll.size > PREROLL_FRAMES) preroll.removeFirst()
                    val loud = frames > CALIBRATION_FRAMES && rms > threshold
                    loudRun = if (loud) loudRun + 1 else 0
                    if (!loud && frames > CALIBRATION_FRAMES) noise = noise * 0.97 + rms * 0.03 // follow the room
                    if (loudRun >= 3) {
                        started = true; onSpeech()
                        preroll.forEach { write(pcm, it) }; preroll.clear()
                    } else if (frames * 20 >= noSpeechMs || stopEarly()) {
                        return null
                    }
                } else {
                    write(pcm, chunk)
                    silenceMs = if (rms > threshold * 0.7) 0 else silenceMs + 20
                    if (silenceMs >= SILENCE_MS || pcm.size() >= RATE * 2 * MAX_MS / 1000 || stopEarly()) break
                }
            }
        } finally {
            runCatching { rec.stop() }
            rec.release()
        }
        if (!started || !active()) return null
        return wav(pcm.toByteArray())
    }

    internal fun rms(b: ShortArray, n: Int): Double {
        var sum = 0.0
        for (i in 0 until n) sum += b[i].toDouble() * b[i]
        return sqrt(sum / n)
    }

    internal fun write(out: ByteArrayOutputStream, s: ShortArray) {
        val bb = ByteBuffer.allocate(s.size * 2).order(ByteOrder.LITTLE_ENDIAN)
        s.forEach { bb.putShort(it) }
        out.write(bb.array())
    }

    private fun wav(pcm: ByteArray): ByteArray = ByteBuffer.allocate(44 + pcm.size).order(ByteOrder.LITTLE_ENDIAN).apply {
        put("RIFF".toByteArray()); putInt(36 + pcm.size); put("WAVE".toByteArray())
        put("fmt ".toByteArray()); putInt(16); putShort(1); putShort(1); putInt(RATE); putInt(RATE * 2); putShort(2); putShort(16)
        put("data".toByteArray()); putInt(pcm.size); put(pcm)
    }.array()
}

/**
 * Barge-in for the overlay: listens while Jarvis speaks and returns as soon as Sir talks over him.
 *
 * The on-device voice comes out of the phone's own speaker, so it reaches the microphone. Three things keep it
 * from interrupting itself: the VOICE_COMMUNICATION source with the platform's echo canceller and noise
 * suppressor where the device has them; the level of Jarvis' own voice is measured first (the first 300 ms, then
 * a slowly falling peak) and Sir must be clearly louder than that; and it has to last [NEED_MS] – a click or a
 * cough is no interruption.
 */
object BargeInListener {
    private const val RATE = 16_000
    private const val FRAME = RATE / 50 // 20 ms
    private const val WARMUP_FRAMES = 15 // 300 ms of Jarvis' own voice, measured before anyone is judged
    private const val NEED_MS = 400
    private const val KEEP_FRAMES = 40 // 800 ms: the words that interrupted are not lost
    private const val MIN_RMS = 1_400.0
    private const val ECHO_FLOOR = 600.0
    private const val RATIO = 2.2

    /** What Sir said so far (16 kHz mono PCM) and the quietest level in it, a stand-in for the room's noise. */
    class Heard(val pcm: ByteArray, val noise: Double)

    /** Blocks until Sir interrupts (returns what he said so far) or [active] turns false / no microphone (null). */
    @SuppressLint("MissingPermission") // callers check RECORD_AUDIO
    fun listen(active: () -> Boolean): Heard? {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(min, FRAME * 2 * 10))
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release(); return null // microphone busy: the answer is simply read out
        }
        val effects = listOfNotNull(
            if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(rec.audioSessionId)?.also { it.enabled = true } else null,
            if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(rec.audioSessionId)?.also { it.enabled = true } else null,
        )
        val recent = ArrayDeque<ShortArray>()
        val levels = ArrayDeque<Double>()
        val buf = ShortArray(FRAME)
        var frames = 0
        var echo = ECHO_FLOOR
        var hits = 0
        try {
            rec.startRecording()
            while (active()) {
                val n = rec.read(buf, 0, FRAME)
                if (n < 0) return null
                if (n == 0) continue
                frames++
                val level = VoiceRecorder.rms(buf, n)
                recent.addLast(buf.copyOf(n)); levels.addLast(level)
                if (recent.size > KEEP_FRAMES) {
                    recent.removeFirst(); levels.removeFirst()
                }
                if (frames <= WARMUP_FRAMES) {
                    echo = max(echo, level); continue
                }
                if (level > max(MIN_RMS, echo * RATIO)) {
                    hits++
                } else {
                    hits = max(0, hits - 1)
                    echo = max(level, echo * 0.995) // follows the peaks of Jarvis' own voice
                }
                if (hits * 20 >= NEED_MS) {
                    val out = ByteArrayOutputStream()
                    recent.forEach { VoiceRecorder.write(out, it) }
                    return Heard(out.toByteArray(), levels.minOrNull() ?: 0.0)
                }
            }
            return null
        } finally {
            runCatching { rec.stop() }
            rec.release()
            effects.forEach { runCatching { it.release() } }
        }
    }
}

/** On-device German text-to-speech for the overlay answers (the call path keeps Jarvis' own voice). */
class Speech(context: Context) {
    private val ready = CompletableDeferred<Boolean>()
    private lateinit var tts: TextToSpeech

    init {
        tts = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val r = tts.setLanguage(Locale.GERMANY)
                if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) tts.setLanguage(Locale.GERMAN)
                tts.setAudioAttributes(
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build(),
                )
            }
            ready.complete(status == TextToSpeech.SUCCESS)
        }
    }

    /** Suspends until everything was spoken (or the coroutine is cancelled, which stops speaking). */
    suspend fun speak(text: String) {
        if (!ready.await()) return
        val parts = chunks(speakable(text))
        if (parts.isEmpty()) return
        val last = "jarvis-last-${System.nanoTime()}"
        suspendCancellableCoroutine<Unit> { cont ->
            fun finish() {
                if (cont.isActive) cont.resume(Unit)
            }
            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) {
                    if (utteranceId == last) finish()
                }
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) = finish()
                override fun onError(utteranceId: String?, errorCode: Int) = finish()
                override fun onStop(utteranceId: String?, interrupted: Boolean) = finish()
            })
            parts.forEachIndexed { i, p ->
                tts.speak(p, if (i == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, Bundle(), if (i == parts.lastIndex) last else "jarvis-$i")
            }
            cont.invokeOnCancellation { tts.stop() }
        }
    }

    fun stop() {
        if (ready.isCompleted) tts.stop()
    }

    fun shutdown() = tts.shutdown()

    private fun chunks(text: String): List<String> {
        val limit = min(TextToSpeech.getMaxSpeechInputLength(), 3_000)
        val out = mutableListOf<String>()
        val cur = StringBuilder()
        for (sentence in text.split(Regex("(?<=[.!?])\\s+"))) {
            if (cur.isNotEmpty() && cur.length + sentence.length + 1 > limit) {
                out += cur.toString(); cur.clear()
            }
            if (cur.isNotEmpty()) cur.append(' ')
            cur.append(sentence.take(limit))
        }
        if (cur.isNotBlank()) out += cur.toString()
        return out
    }

    companion object {
        // Local file paths (a generated image on the Mac …) – same rule as the core's speakable().
        private val FILE_PATH = Regex(
            "(?<![\\w.:/])(?:~|/(?:Users|private|tmp|var|Volumes|home|opt))(?:/(?:[^\\s/`]+(?: [^\\s/`]+)*(?=/)|[^\\s/`]+))+",
        )

        /** Markdown, code and file paths are for the screen, not the ear. */
        fun speakable(text: String): String = text
            .replace(Regex("```[\\s\\S]*?```"), " ")
            .replace(FILE_PATH, "")
            .replace(Regex("\\[([^\\]]+)]\\([^)]+\\)"), "$1")
            .replace(Regex("https?://\\S+"), "Link")
            .replace(Regex("[*_#>`|]+"), "")
            .replace(Regex("\\s+"), " ")
            .trim()
    }
}

/** Pauses music/podcasts while Jarvis listens and speaks, like the system assistant. */
class AudioFocus(context: Context) {
    private val am = context.getSystemService(AudioManager::class.java)
    private val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
        .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANT).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
        .setOnAudioFocusChangeListener { }
        .build()
    private var held = false

    fun acquire() {
        if (!held) held = am.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    fun release() {
        if (held) am.abandonAudioFocusRequest(request)
        held = false
    }
}

/** Short "I'm listening" tone. */
object Earcon {
    fun listening() {
        runCatching {
            val tone = ToneGenerator(AudioManager.STREAM_MUSIC, 45)
            tone.startTone(ToneGenerator.TONE_PROP_BEEP, 110)
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({ tone.release() }, 400)
        }
    }
}
