package systems.pje.jarvis_mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.pje.jarvis_mobile.assist.WakeDetector

class WakeDetectorTest {
    @Test fun defaultSensitivityGivesTheTestedThreshold() {
        assertEquals("▁HE Y ▁JA R VI S :2.0 #0.152 @HEY_JARVIS", WakeDetector.keywordLine(0.6f))
    }

    @Test fun higherSensitivityLowersTheThreshold() {
        fun threshold(s: Float) = WakeDetector.keywordLine(s).substringAfter('#').substringBefore(' ').toFloat()
        assertTrue(threshold(0.9f) < threshold(0.5f))
        assertTrue(threshold(0f) <= 0.35f && threshold(1f) > 0f)
        assertEquals(threshold(1f), threshold(5f), 0f) // clamped
    }
}
