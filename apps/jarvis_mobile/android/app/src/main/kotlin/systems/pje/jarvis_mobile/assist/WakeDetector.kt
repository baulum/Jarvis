package systems.pje.jarvis_mobile.assist

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineStream
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig

private const val TAG = "JarvisWake"
private const val RATE = 16_000
private const val CHUNK = 1_280 // 80 ms

/**
 * On-device "Hey Jarvis" detector: sherpa-onnx keyword spotter (small streaming zipformer, Apache-2.0) fed by an
 * [AudioRecord]. Needs no account, key or network – the model ships in `assets/kws`.
 *
 * The phrase is open vocabulary: it is spelled out in BPE tokens of that model (`tokens.txt`), no training needed.
 * Not thread-safe except that [start]/[stop]/[release] may be called from the main thread while the reader runs.
 */
class WakeDetector private constructor(
    private val spotter: KeywordSpotter,
    private val sensitivity: Float,
    private val onWake: () -> Unit,
    private val onError: (Throwable) -> Unit,
) {
    companion object {
        /** "HEY JARVIS" in the BPE pieces of the model's vocabulary (see assets/kws/keywords.txt). */
        private const val PHRASE = "▁HE Y ▁JA R VI S"

        /**
         * Sensitivity 0..1 → acceptance threshold of the spotter (lower = triggers more easily).
         * 0.6 → 0.15: in a TTS test, that caught three of four voices on "Hey Jarvis" and none of "Hello Jarvis",
         * "Hey Travis", "Hey Siri", "Jarvis" or plain speech.
         */
        fun keywordLine(sensitivity: Float): String {
            val threshold = 0.35f - 0.33f * sensitivity.coerceIn(0f, 1f)
            return "$PHRASE :2.0 #${"%.3f".format(java.util.Locale.ROOT, threshold)} @HEY_JARVIS"
        }

        /** Loads the model (some hundred ms of file and ONNX work) – call off the main thread. */
        fun build(c: Context, sensitivity: Float, onWake: () -> Unit, onError: (Throwable) -> Unit): WakeDetector {
            val config = KeywordSpotterConfig(
                featConfig = FeatureConfig(sampleRate = RATE, featureDim = 80),
                modelConfig = OnlineModelConfig(
                    transducer = OnlineTransducerModelConfig(
                        encoder = "kws/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
                        decoder = "kws/decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
                        joiner = "kws/joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
                    ),
                    tokens = "kws/tokens.txt",
                    numThreads = 1,
                    provider = "cpu",
                    modelType = "zipformer2",
                ),
                keywordsFile = "kws/keywords.txt",
            )
            return WakeDetector(KeywordSpotter(c.assets, config), sensitivity, onWake, onError)
        }
    }

    private val lock = Any()
    private var record: AudioRecord? = null
    private var reader: Thread? = null
    @Volatile private var running = false

    /** Opens the microphone and starts reading. Throws when the microphone is not available (busy, no permission). */
    @SuppressLint("MissingPermission")
    fun start() = synchronized(lock) {
        if (running) return
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        check(min > 0) { "Mikrofon liefert keine Puffergröße ($min)" }
        val rec = AudioRecord(
            MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            maxOf(min, CHUNK * 2 * 4),
        )
        try {
            check(rec.state == AudioRecord.STATE_INITIALIZED) { "Mikrofon nicht verfügbar (Berechtigung fehlt oder belegt)" }
            rec.startRecording()
            check(rec.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "Mikrofon belegt (Anruf oder andere App)" }
        } catch (e: Throwable) {
            runCatching { rec.release() }; throw e
        }
        record = rec
        running = true
        reader = Thread({ read(rec) }, "jarvis-wake").apply { isDaemon = true; start() }
    }

    /** Stops reading and releases the microphone; the model stays loaded for the next [start]. */
    fun stop() {
        val t: Thread?
        synchronized(lock) {
            if (!running && reader == null) return
            running = false
            t = reader; reader = null
        }
        if (t !== Thread.currentThread()) runCatching { t?.join(500) }
        synchronized(lock) {
            record?.let { runCatching { it.stop() }; runCatching { it.release() } }
            record = null
        }
    }

    fun release() {
        stop()
        runCatching { spotter.release() }
    }

    private fun read(rec: AudioRecord) {
        var stream: OnlineStream? = null
        try {
            stream = spotter.createStream(keywordLine(sensitivity))
            val pcm = ShortArray(CHUNK)
            val samples = FloatArray(CHUNK)
            while (running) {
                val n = rec.read(pcm, 0, CHUNK)
                if (n < 0) throw IllegalStateException("Mikrofon lesen fehlgeschlagen ($n)")
                if (n == 0) continue
                val chunk = if (n == CHUNK) samples else FloatArray(n)
                for (i in 0 until n) chunk[i] = pcm[i] / 32768f
                stream.acceptWaveform(chunk, RATE)
                while (running && spotter.isReady(stream)) {
                    spotter.decode(stream)
                    val hit = spotter.getResult(stream).keyword
                    if (hit.isNotEmpty()) {
                        Log.i(TAG, "keyword $hit")
                        spotter.reset(stream)
                        running = false
                        onWake()
                        return
                    }
                }
            }
        } catch (e: Throwable) {
            if (running) {
                running = false
                onError(e)
            }
        } finally {
            runCatching { stream?.release() }
        }
    }
}
