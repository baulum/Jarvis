package systems.pje.jarvis_mobile.notify

import java.net.URLEncoder

/**
 * Where a tap on a notification leads. The core sends `link` (jarvis://job/<id>, …) plus `target_type`/`target_id`;
 * the pieces are used whenever the link is missing or only points to a list/home, so every category opens the
 * detail view of what it is about (job, task, approval, call, chat) and never just the start page.
 */
object AlertLinks {
    private val GENERIC = setOf("jarvis://home", "jarvis://activity", "jarvis://")
    private val TYPES = setOf("job", "task", "approval", "call", "chat")

    fun resolve(link: String, targetType: String, targetId: String): String {
        val target = if (targetType in TYPES && targetId.isNotBlank()) {
            "jarvis://$targetType/" + URLEncoder.encode(targetId, "UTF-8")
        } else null
        return when {
            link.isNotBlank() && link !in GENERIC -> link
            target != null -> target
            link.isNotBlank() -> link
            else -> "jarvis://activity"
        }
    }
}
