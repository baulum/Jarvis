package systems.pje.jarvis_mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.pje.jarvis_mobile.phone.AppMatcher
import systems.pje.jarvis_mobile.phone.AppMatcher.App
import systems.pje.jarvis_mobile.phone.AppMatcher.Match
import systems.pje.jarvis_mobile.phone.UiNodeView
import systems.pje.jarvis_mobile.phone.UiTree

private class FakeNode(
    override val className: String? = "android.widget.TextView",
    override val text: String? = null,
    override val contentDescription: String? = null,
    override val viewId: String? = null,
    override val clickable: Boolean = false,
    override val longClickable: Boolean = false,
    override val scrollable: Boolean = false,
    override val editable: Boolean = false,
    override val focused: Boolean = false,
    override val checkable: Boolean = false,
    override val checked: Boolean = false,
    override val enabled: Boolean = true,
    override val visible: Boolean = true,
    override val password: Boolean = false,
    override val bounds: IntArray = intArrayOf(0, 0, 100, 50),
    val kids: List<FakeNode> = emptyList(),
) : UiNodeView {
    override val childCount get() = kids.size
    override fun child(index: Int): UiNodeView? = kids.getOrNull(index)
}

class PhoneControlTest {
    private val apps = listOf(
        App("WhatsApp", "com.whatsapp"),
        App("WhatsApp Business", "com.whatsapp.w4b"),
        App("Kalender", "com.google.android.calendar"),
        App("Chrome", "com.android.chrome"),
        App("YouTube", "com.google.android.youtube"),
        App("YouTube Music", "com.google.android.apps.youtube.music"),
        App("Einstellungen", "com.android.settings"),
    )

    // ------------------------------------------------------------------ app name resolution
    @Test
    fun whatsappResolvesToComWhatsapp() {
        assertEquals(Match.Found(apps[0]), AppMatcher.resolve("WhatsApp", apps))
        assertEquals(Match.Found(apps[0]), AppMatcher.resolve("whatsapp", apps))
        assertEquals(Match.Found(apps[0]), AppMatcher.resolve("Whats App", apps))
    }

    @Test
    fun exactLabelBeatsLongerLabelsWithTheSamePrefix() {
        assertEquals(Match.Found(apps[4]), AppMatcher.resolve("youtube", apps))
        assertEquals(Match.Found(apps[5]), AppMatcher.resolve("YouTube Music", apps))
    }

    @Test
    fun packageNameIsTakenAsGiven() {
        assertEquals(Match.Found(apps[1]), AppMatcher.resolve("com.whatsapp.w4b", apps))
    }

    @Test
    fun typosAndUmlautsStillFindTheApp() {
        assertEquals(Match.Found(apps[2]), AppMatcher.resolve("Kalendar", apps))
        assertEquals(Match.Found(apps[0]), AppMatcher.resolve("whatsap", apps))
        assertEquals("uber", AppMatcher.norm("Über"))
    }

    @Test
    fun unknownAppIsNone() {
        assertEquals(Match.None, AppMatcher.resolve("Signal", apps))
        assertEquals(Match.None, AppMatcher.resolve("  ", apps))
    }

    @Test
    fun equallyGoodMatchesAreReportedAsAmbiguous() {
        val two = listOf(App("Fotos", "com.google.android.apps.photos"), App("Fotos", "com.samsung.gallery"))
        val m = AppMatcher.resolve("Fotos", two)
        assertTrue(m is Match.Ambiguous && m.candidates.size == 2)
    }

    // ------------------------------------------------------------------ screen tree
    private fun screen() = FakeNode(
        className = "android.widget.FrameLayout",
        kids = listOf(
            FakeNode(text = "Anmelden", className = "android.widget.TextView"),
            FakeNode(className = "android.widget.EditText", editable = true, clickable = true, viewId = "com.x:id/user", text = "sir@example.org"),
            FakeNode(className = "android.widget.EditText", editable = true, password = true, text = "geheim123", contentDescription = "Passwort geheim123"),
            FakeNode(className = "android.widget.Button", clickable = true, text = "Weiter", viewId = "com.x:id/next"),
            FakeNode(className = "android.view.View", kids = listOf(FakeNode(text = "versteckt", visible = false))),
        ),
    )

    @Test
    fun treeKeepsInteractiveAndTextNodesWithPathIds() {
        val built = UiTree.build(screen())
        val ids = (0 until built.nodes.length()).map { built.nodes.getJSONObject(it).getString("id") }
        assertEquals(listOf("0.0", "0.1", "0.2", "0.3"), ids)
        val button = built.nodes.getJSONObject(3)
        assertEquals("Button", button.getString("cls"))
        assertEquals("next", button.getString("res"))
        assertTrue(button.getBoolean("click"))
        assertEquals(4, button.getJSONArray("bounds").length())
    }

    @Test
    fun passwordFieldsNeverLeakTextOrDescription() {
        val built = UiTree.build(screen())
        val pw = built.nodes.getJSONObject(2)
        assertTrue(pw.getBoolean("password"))
        assertFalse(pw.has("text"))
        assertFalse(pw.has("desc"))
        assertFalse(built.nodes.toString().contains("geheim123"))
        assertFalse(built.fingerprints.getValue("0.2").contains("geheim123"))
    }

    @Test
    fun invisibleNodesAreSkippedAndLargeScreensAreTruncated() {
        assertFalse(UiTree.build(screen()).nodes.toString().contains("versteckt"))
        val many = FakeNode(kids = (1..UiTree.MAX_NODES + 50).map { FakeNode(text = "Eintrag $it") })
        val built = UiTree.build(many)
        assertEquals(UiTree.MAX_NODES, built.nodes.length())
        assertTrue(built.truncated)
    }

    @Test
    fun findFollowsThePathAndRejectsBadIds() {
        val root = screen()
        assertEquals("Weiter", UiTree.find(root, "0.3")?.text)
        assertEquals(root, UiTree.find(root, "0"))
        assertNull(UiTree.find(root, "0.9"))
        assertNull(UiTree.find(root, "1.0"))
        assertNull(UiTree.find(root, "0..1"))
        assertNull(UiTree.find(root, "0.x"))
        assertNull(UiTree.find(root, ""))
    }

    @Test
    fun fingerprintChangesWhenTheScreenChanges() {
        val a = UiTree.fingerprint(FakeNode(text = "Weiter", viewId = "com.x:id/next"))
        val b = UiTree.fingerprint(FakeNode(text = "Abbrechen", viewId = "com.x:id/next"))
        assertFalse(a == b)
    }

    @Test
    fun passwordInputTypesAreRecognised() {
        assertTrue(UiTree.isPasswordInputType(0x00000081)) // text password
        assertTrue(UiTree.isPasswordInputType(0x00000091)) // visible password
        assertTrue(UiTree.isPasswordInputType(0x000000e1)) // web password
        assertTrue(UiTree.isPasswordInputType(0x00000012)) // number password
        assertFalse(UiTree.isPasswordInputType(0x00000001)) // plain text
        assertFalse(UiTree.isPasswordInputType(0x00000002)) // plain number
    }
}
