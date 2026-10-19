package systems.pje.jarvis_mobile

import org.junit.Assert.assertEquals
import org.junit.Test
import systems.pje.jarvis_mobile.assist.Speech

class AssistSpeechTest {
    @Test
    fun markdownIsNotReadAloud() {
        val text = "**Erledigt**, Sir.\n\n```bash\nls -la\n```\nDetails: [Bericht](https://example.org/x) – siehe `README`."
        assertEquals("Erledigt, Sir. Details: Bericht – siehe README.", Speech.speakable(text))
    }

    @Test
    fun bareUrlsBecomeLink() {
        assertEquals("Die Seite ist unter Link online.", Speech.speakable("Die Seite ist unter https://pje.systems/neu online."))
    }
}
