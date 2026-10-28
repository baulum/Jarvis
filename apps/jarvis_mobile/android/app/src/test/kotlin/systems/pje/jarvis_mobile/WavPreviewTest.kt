package systems.pje.jarvis_mobile

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import systems.pje.jarvis_mobile.assist.WavPreview
import java.nio.ByteBuffer
import java.nio.ByteOrder

class WavPreviewTest {
    private fun wav(rate: Int, samples: ShortArray, bits: Int = 16, extra: ByteArray = ByteArray(0)): ByteArray {
        val data = ByteBuffer.allocate(samples.size * 2).order(ByteOrder.LITTLE_ENDIAN).apply { samples.forEach { putShort(it) } }.array()
        val b = ByteBuffer.allocate(12 + 24 + extra.size + 8 + data.size).order(ByteOrder.LITTLE_ENDIAN)
        b.put("RIFF".toByteArray()).putInt(b.capacity() - 8).put("WAVE".toByteArray())
        b.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1).putInt(rate).putInt(rate * 2).putShort(2).putShort(bits.toShort())
        b.put(extra)
        b.put("data".toByteArray()).putInt(data.size).put(data)
        return b.array()
    }

    @Test
    fun coreHoerprobeIsParsed() {
        val pcm = WavPreview.parse(wav(24_000, shortArrayOf(1, -2, 3)))!!
        assertEquals(24_000, pcm.rate)
        assertEquals(1, pcm.channels)
        assertArrayEquals(byteArrayOf(1, 0, -2, -1, 3, 0), pcm.data)
    }

    @Test
    fun unknownChunksAreSkipped() {
        val list = ByteBuffer.allocate(10).order(ByteOrder.LITTLE_ENDIAN).put("LIST".toByteArray()).putInt(2).putShort(0).array()
        assertEquals(2, WavPreview.parse(wav(16_000, shortArrayOf(7), extra = list))!!.data.size)
    }

    @Test
    fun anythingElseIsRejected() {
        assertNull(WavPreview.parse("{\"detail\": \"x\"}".toByteArray()))
        assertNull(WavPreview.parse(wav(24_000, shortArrayOf(1), bits = 8)))
    }
}
