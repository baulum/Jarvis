package systems.pje.jarvis_mobile

import org.junit.Assert.assertEquals
import org.junit.Test
import systems.pje.jarvis_mobile.notify.AlertLinks

class AlertLinksTest {
    @Test fun specificLinkWins() {
        assertEquals("jarvis://job/j1", AlertLinks.resolve("jarvis://job/j1", "job", "j1"))
    }

    @Test fun genericLinkIsReplacedByTarget() {
        assertEquals("jarvis://job/j1", AlertLinks.resolve("jarvis://home", "job", "j1"))
        assertEquals("jarvis://task/t1", AlertLinks.resolve("", "task", "t1"))
    }

    @Test fun withoutTargetFallsBackToActivity() {
        assertEquals("jarvis://activity", AlertLinks.resolve("", "", ""))
        assertEquals("jarvis://home", AlertLinks.resolve("jarvis://home", "", ""))
    }

    @Test fun unknownTypeIsIgnored() {
        assertEquals("jarvis://activity", AlertLinks.resolve("", "evil", "x"))
    }
}
