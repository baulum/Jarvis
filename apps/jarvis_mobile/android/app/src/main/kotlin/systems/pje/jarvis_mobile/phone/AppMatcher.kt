package systems.pje.jarvis_mobile.phone

import java.text.Normalizer

/**
 * Resolves what Sir says ("WhatsApp", "den Kalender", "youtube music") to an installed, launchable app.
 * Pure Kotlin – no Android classes, so it is unit-tested on the JVM.
 */
object AppMatcher {
    data class App(val label: String, val pkg: String)

    sealed class Match {
        data class Found(val app: App) : Match()
        data class Ambiguous(val candidates: List<App>) : Match()
        data object None : Match()
    }

    private const val MAX_CANDIDATES = 5

    /** lower case, no diacritics, only letters and digits ("WhatsApp Business" → "whatsappbusiness"). */
    fun norm(s: String): String =
        Normalizer.normalize(s.lowercase(), Normalizer.Form.NFD).filter { it.code !in 0x300..0x36F && it.isLetterOrDigit() }

    private fun words(s: String): List<String> =
        Normalizer.normalize(s.lowercase(), Normalizer.Form.NFD)
            .split(Regex("[^\\p{L}\\p{N}]+")).map { norm(it) }.filter { it.isNotEmpty() }

    private fun levenshtein(a: String, b: String): Int {
        var prev = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val cur = IntArray(b.length + 1).also { it[0] = i }
            for (j in 1..b.length) {
                cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
            }
            prev = cur
        }
        return prev[b.length]
    }

    /** 0 = no match; higher is better. */
    fun score(query: String, app: App): Int {
        val q = norm(query)
        if (q.isEmpty()) return 0
        val label = norm(app.label)
        if (label == q) return 100
        if (label.startsWith(q)) return 85
        if (words(app.label).any { it == q }) return 80
        if (words(app.label).any { it.startsWith(q) }) return 70
        if (q.length >= 3 && label.contains(q)) return 60
        // "kalender" ↔ "kalendar", "whatsap" ↔ "whatsapp": tolerate small typos / speech-to-text slips
        val tolerance = if (q.length >= 8) 2 else if (q.length >= 4) 1 else 0
        if (tolerance > 0) {
            val candidates = words(app.label) + label
            if (candidates.any { levenshtein(q, it) <= tolerance }) return 50
        }
        if (q.length >= 4 && norm(app.pkg).contains(q)) return 40
        return 0
    }

    fun resolve(query: String, apps: List<App>): Match {
        val q = query.trim()
        apps.firstOrNull { it.pkg.equals(q, ignoreCase = true) }?.let { return Match.Found(it) }
        val scored = apps.map { it to score(q, it) }.filter { it.second > 0 }
        if (scored.isEmpty()) return Match.None
        val best = scored.maxOf { it.second }
        val top = scored.filter { it.second == best }.map { it.first }.sortedBy { it.label.lowercase() }
        if (top.size == 1) return Match.Found(top.first())
        // "WhatsApp" vs "WhatsApp Business": when the base name is a prefix of all the others, Sir means the base app
        val base = top.minBy { norm(it.label).length }
        if (top.count { norm(it.label) == norm(base.label) } == 1 && top.all { norm(it.label).startsWith(norm(base.label)) }) {
            return Match.Found(base)
        }
        return Match.Ambiguous(top.take(MAX_CANDIDATES))
    }
}
