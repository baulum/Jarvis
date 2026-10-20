package systems.pje.jarvis_mobile.phone

import org.json.JSONArray
import org.json.JSONObject

/** What UiTree needs to know about one on-screen element (an adapter wraps AccessibilityNodeInfo). */
interface UiNodeView {
    val className: String?
    val text: String?
    val contentDescription: String?
    val viewId: String?
    val clickable: Boolean
    val longClickable: Boolean
    val scrollable: Boolean
    val editable: Boolean
    val focused: Boolean
    val checkable: Boolean
    val checked: Boolean
    val enabled: Boolean
    val visible: Boolean
    val password: Boolean
    /** left, top, right, bottom in screen pixels */
    val bounds: IntArray
    val childCount: Int
    fun child(index: Int): UiNodeView?
}

/**
 * Turns the screen hierarchy into the compact element list Jarvis reads, and finds elements again by id.
 *
 * Element ids are child-index paths from the window root ("0.2.1"): stable while the layout stays the same,
 * and every id carries a fingerprint (class, view id, text) so a changed screen is detected instead of tapping
 * whatever now sits at that position. Password fields never expose text or description (neither read nor filled).
 */
object UiTree {
    const val MAX_NODES = 400
    const val MAX_TEXT = 200
    private const val MAX_DEPTH = 40

    class Built(val nodes: JSONArray, val truncated: Boolean, val fingerprints: Map<String, String>)

    fun build(root: UiNodeView): Built {
        val nodes = JSONArray()
        val prints = LinkedHashMap<String, String>()
        var truncated = false

        fun walk(n: UiNodeView, id: String, depth: Int) {
            if (!n.visible || depth > MAX_DEPTH) return
            val interesting = n.password || n.clickable || n.longClickable || n.scrollable || n.editable || n.checkable ||
                !n.text.isNullOrBlank() || !n.contentDescription.isNullOrBlank()
            if (interesting) {
                if (nodes.length() >= MAX_NODES) { truncated = true; return }
                nodes.put(describe(n, id))
                prints[id] = fingerprint(n)
            }
            for (i in 0 until n.childCount) {
                val c = n.child(i) ?: continue
                walk(c, "$id.$i", depth + 1)
                if (truncated) return
            }
        }
        walk(root, "0", 0)
        return Built(nodes, truncated, prints)
    }

    private fun clip(s: String?): String? = s?.trim()?.takeIf { it.isNotEmpty() }?.let { if (it.length > MAX_TEXT) it.take(MAX_TEXT) + "…" else it }

    fun describe(n: UiNodeView, id: String): JSONObject {
        val o = JSONObject().put("id", id).put("cls", (n.className ?: "").substringAfterLast('.'))
        if (n.password) {
            o.put("password", true)  // no text, no description: the content stays on the phone
        } else {
            clip(n.text)?.let { o.put("text", it) }
            clip(n.contentDescription)?.let { o.put("desc", it) }
        }
        n.viewId?.substringAfter(":id/", "")?.takeIf { it.isNotEmpty() }?.let { o.put("res", it) }
        if (n.clickable) o.put("click", true)
        if (n.longClickable) o.put("long_click", true)
        if (n.scrollable) o.put("scroll", true)
        if (n.editable) o.put("edit", true)
        if (n.focused) o.put("focused", true)
        if (n.checkable) o.put("checked", n.checked)
        if (!n.enabled) o.put("enabled", false)
        o.put("bounds", JSONArray(n.bounds.toList()))
        return o
    }

    fun fingerprint(n: UiNodeView): String =
        if (n.password) "${n.className}|password"
        else "${n.className}|${n.viewId}|${(n.text ?: n.contentDescription ?: "").trim().take(40)}"

    /** Follows an id path ("0.2.1") down from [root]; null when the structure no longer has that element. */
    fun find(root: UiNodeView, id: String): UiNodeView? {
        val parts = id.split('.')
        if (parts.firstOrNull() != "0" || parts.any { it.isEmpty() || !it.all(Char::isDigit) }) return null
        var cur: UiNodeView = root
        for (p in parts.drop(1)) {
            val i = p.toIntOrNull() ?: return null
            if (i >= cur.childCount) return null
            cur = cur.child(i) ?: return null
        }
        return cur
    }

    /** Text input types that mean "password" (text, web, visible and numeric password variations). */
    fun isPasswordInputType(inputType: Int): Boolean {
        val cls = inputType and 0x0000000f
        val variation = inputType and 0x00000ff0
        return when (cls) {
            0x1 -> variation == 0x80 || variation == 0x90 || variation == 0xe0 // text: password, visible password, web password
            0x2 -> variation == 0x10 // number password
            else -> false
        }
    }
}
