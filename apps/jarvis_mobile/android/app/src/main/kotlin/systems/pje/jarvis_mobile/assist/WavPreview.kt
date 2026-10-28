package systems.pje.jarvis_mobile.assist

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Plays a short WAV from the core on the media stream – the Hörprobe of a personality (Einstellungen →
 * Persönlichkeit). One clip at a time: a new one (or [stop]) ends the previous.
 */
object WavPreview {
    class Pcm(val rate: Int, val channels: Int, val data: ByteArray)

    /** 16-bit PCM of a RIFF/WAVE file, or null when it is not one. */
    fun parse(wav: ByteArray): Pcm? {
        if (wav.size < 12 || String(wav, 0, 4, Charsets.US_ASCII) != "RIFF" || String(wav, 8, 4, Charsets.US_ASCII) != "WAVE") return null
        val buf = ByteBuffer.wrap(wav).order(ByteOrder.LITTLE_ENDIAN)
        var pos = 12
        var rate = 0
        var channels = 0
        while (pos + 8 <= wav.size) {
            val id = String(wav, pos, 4, Charsets.US_ASCII)
            val size = buf.getInt(pos + 4).toLong() and 0xFFFFFFFFL
            val body = pos + 8
            if (id == "fmt " && body + 16 <= wav.size) {
                val format = buf.getShort(body).toInt()
                channels = buf.getShort(body + 2).toInt()
                rate = buf.getInt(body + 4)
                val bits = buf.getShort(body + 14).toInt()
                if (format != 1 || bits != 16 || channels !in 1..2 || rate <= 0) return null
            } else if (id == "data") {
                if (rate == 0) return null
                val end = minOf(wav.size.toLong(), body + size).toInt()
                val frame = 2 * channels
                return Pcm(rate, channels, wav.copyOfRange(body, end - (end - body) % frame))
            }
            pos = (body + size + (size and 1)).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
        }
        return null
    }

    @Volatile private var track: AudioTrack? = null

    /** Suspends until the clip has been played (or [stop]ped). False when [wav] is not playable. */
    suspend fun play(wav: ByteArray): Boolean = withContext(Dispatchers.IO) {
        val pcm = parse(wav) ?: return@withContext false
        if (pcm.data.isEmpty()) return@withContext false
        stop()
        val t = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(pcm.rate)
                    .setChannelMask(if (pcm.channels == 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(pcm.data.size)
            .build()
        track = t
        try {
            t.write(pcm.data, 0, pcm.data.size)
            t.play()
            val frames = pcm.data.size / (2 * pcm.channels)
            while (track === t && t.playState == AudioTrack.PLAYSTATE_PLAYING && t.playbackHeadPosition < frames) delay(50)
            true
        } finally {
            if (track === t) track = null
            runCatching { t.stop() }
            t.release()
        }
    }

    fun stop() {
        val t = track
        track = null
        runCatching { t?.stop() }
    }
}
