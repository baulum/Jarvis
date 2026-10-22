package systems.pje.jarvis_mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.pje.jarvis_mobile.assist.BargeInDetector

class BargeInDetectorTest {
    private val echo = 900.0 // Jarvis' own voice as it reaches the microphone
    private val sir = 5_000.0

    /** Feeds [ms] of constant [level]; the frame (1-based) at which the detector fired, or null. */
    private fun BargeInDetector.run(level: Double, ms: Int): Int? {
        for (i in 1..ms / BargeInDetector.FRAME_MS) if (feed(level)) return i
        return null
    }

    private fun warmedUp() = BargeInDetector().also { assertEquals(null, it.run(echo, 600)) }

    @Test
    fun ownVoiceNeverInterrupts() {
        val d = BargeInDetector()
        assertEquals(null, d.run(echo, 10_000))
    }

    @Test
    fun loudOwnVoiceIsLearnedDuringWarmup() {
        // a loud speaker: 2 000 is above MIN_RMS, but not above 2.2 × the echo level learned in the first 300 ms
        val d = BargeInDetector()
        assertEquals(null, d.run(2_000.0, 6_000))
    }

    @Test
    fun sustainedSpeechInterruptsAfterTheMinimumDuration() {
        val d = warmedUp()
        val frame = d.run(sir, 1_000)
        assertEquals(BargeInDetector.NEED_MS / BargeInDetector.FRAME_MS, frame) // 400 ms – not sooner, not much later
    }

    @Test
    fun minimumDurationIsWithinTheDesignedRange() {
        assertTrue(BargeInDetector.NEED_MS in 250..400)
    }

    @Test
    fun aClickOrCoughDoesNotInterrupt() {
        val d = warmedUp()
        assertEquals(null, d.run(sir, 160)) // 8 frames
        assertEquals(null, d.run(echo, 400))
        assertEquals(null, d.run(sir, 160))
    }

    @Test
    fun shortGapsInSpeechDoNotResetTheCount() {
        val d = warmedUp()
        assertEquals(null, d.run(sir, 160))
        assertEquals(null, d.run(echo, 20)) // one weak frame between two words
        assertTrue(d.run(sir, 400) != null)
    }

    @Test
    fun quietSpeechBelowTheAbsoluteMinimumIsIgnored() {
        val d = warmedUp()
        assertEquals(null, d.run(BargeInDetector.MIN_RMS - 100, 3_000))
    }

    @Test
    fun withoutEchoCancellationSirMustBeLouder() {
        val strict = BargeInDetector(BargeInDetector.RATIO_WITHOUT_AEC)
        assertEquals(null, strict.run(echo, 600))
        val level = echo * 2.6 // clearly above the echo with AEC, not without
        assertEquals(null, strict.run(level, 1_000))
        val lenient = warmedUp()
        assertTrue(lenient.run(level, 1_000) != null)
    }

    @Test
    fun anInterruptionRightAfterTheWarmupIsStillHeard() {
        val d = warmedUp()
        assertFalse(d.feed(echo))
        assertTrue(d.run(sir, 600) != null)
    }
}
