package systems.pje.jarvis_mobile.phone

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.util.Log
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import systems.pje.jarvis_mobile.net.Gateway

private const val TAG = "JarvisPhoneControl"

/**
 * Executes the `phone_command` pushes from the Jarvis core and reports the outcome to
 * `POST /api/mobile/phone/result` – the same encrypted push + HTTP rail as the location request.
 * Arguments, texts and screen contents are never logged.
 */
object PhoneControl {
    private val serial = Mutex() // one command at a time, like the core does

    suspend fun handle(context: Context, msg: JSONObject) {
        val cmdId = msg.optString("cmd_id")
        if (cmdId.length < 8) return
        val action = msg.optString("action")
        val args = msg.optJSONObject("args") ?: JSONObject()
        val result = serial.withLock {
            try {
                JSONObject().put("ok", true).put("data", execute(context, action, args))
            } catch (e: PhoneException) {
                JSONObject().put("ok", false).put("error", e.code).apply { if (e.message?.isNotBlank() == true) put("message", e.message) }
            } catch (e: Exception) {
                Log.w(TAG, "Command $action failed: ${e.javaClass.simpleName}")
                JSONObject().put("ok", false).put("error", "action_failed").put("message", e.javaClass.simpleName)
            }
        }
        report(context, cmdId, result)
    }

    private suspend fun report(context: Context, cmdId: String, result: JSONObject) {
        val body = result.put("cmd_id", cmdId).toString()
        val gw = Gateway.get(context)
        repeat(2) { attempt ->
            val sent = runCatching { gw.request("POST", "/api/mobile/phone/result", body, readTimeoutS = 30); true }
                .getOrElse { Log.w(TAG, "Result upload failed: ${it.message}"); false }
            if (sent) return
            if (attempt == 0) delay(1000)
        }
    }

    suspend fun execute(context: Context, action: String, args: JSONObject): JSONObject = when (action) {
        "list_apps" -> listApps(context)
        "open_app" -> openApp(context, args.optString("name"), args.optString("package"))
        "ui_tree" -> service(context).uiTree()
        "tap" -> {
            val id = args.optString("element_id").ifEmpty { null }
            if (id == null && !(args.has("x") && args.has("y"))) throw PhoneException("bad_request", "element_id oder x/y fehlt")
            service(context).tap(id, args.optDouble("x").toFloat(), args.optDouble("y").toFloat())
            JSONObject().put("tapped", true)
        }
        "type_text" -> {
            val text = args.optString("text")
            if (text.isEmpty()) throw PhoneException("bad_request", "text fehlt")
            service(context).typeText(text, args.optString("element_id").ifEmpty { null }, args.optBoolean("append", false))
            JSONObject().put("typed", true).put("length", text.length) // never echo the text back
        }
        "scroll" -> {
            service(context).scroll(args.optString("direction"), args.optString("element_id").ifEmpty { null })
            JSONObject().put("scrolled", true)
        }
        "key" -> {
            service(context).key(args.optString("key"))
            JSONObject().put("pressed", args.optString("key"))
        }
        "screenshot" -> service(context).screenshot()
        else -> throw PhoneException("bad_request", "Unbekannter Befehl")
    }

    private fun service(@Suppress("UNUSED_PARAMETER") context: Context): PhoneControlService {
        PhoneControlService.instance?.let { return it }
        throw PhoneException("accessibility_disabled")
    }

    // ------------------------------------------------------------------ apps (no accessibility needed)
    fun installedApps(context: Context): List<AppMatcher.App> {
        val pm = context.packageManager
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return pm.queryIntentActivities(main, PackageManager.MATCH_ALL)
            .map { AppMatcher.App(it.loadLabel(pm).toString(), it.activityInfo.packageName) }
            .distinctBy { it.pkg }
            .sortedBy { it.label.lowercase() }
    }

    private fun listApps(context: Context): JSONObject {
        val apps = installedApps(context)
        return JSONObject()
            .put("count", apps.size)
            .put("apps", JSONArray(apps.map { JSONObject().put("label", it.label).put("package", it.pkg) }))
    }

    private suspend fun openApp(context: Context, name: String, pkg: String): JSONObject {
        val apps = installedApps(context)
        val app = when (val m = AppMatcher.resolve(pkg.ifBlank { name }, apps)) {
            is AppMatcher.Match.Found -> m.app
            is AppMatcher.Match.Ambiguous -> throw PhoneException(
                "app_ambiguous", "Treffer: " + m.candidates.joinToString("; ") { "${it.label} (${it.pkg})" } + " – mit package erneut aufrufen.",
            )
            AppMatcher.Match.None -> throw PhoneException("app_not_found", "Gesucht: „${pkg.ifBlank { name }}“.")
        }
        val intent = context.packageManager.getLaunchIntentForPackage(app.pkg)
            ?: throw PhoneException("app_not_found", "${app.label} hat keinen Starteintrag.")
        val svc = PhoneControlService.instance
        try {
            // the accessibility service may start activities from the background; the app context may be blocked by Android
            (svc ?: context).startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED))
        } catch (e: Exception) {
            throw PhoneException("action_failed", "Android hat den Start von ${app.label} verweigert (${e.javaClass.simpleName}).")
        }
        val out = JSONObject().put("launched", true).put("label", app.label).put("package", app.pkg)
        if (svc != null) {
            delay(1500)
            val front = svc.foregroundPackage()
            out.put("foreground", front == app.pkg)
            if (front != app.pkg) out.put("note", "Start ausgelöst, die App ist aber noch nicht im Vordergrund (lädt noch, oder Android hat den Start blockiert). Mit phone_ui_tree prüfen.")
        } else {
            out.put("foreground", JSONObject.NULL)
            out.put("note", "Start ausgelöst, aber nicht überprüfbar: Die Bedienungshilfe (Handy-Steuerung) ist nicht aktiviert; Android kann Starts aus dem Hintergrund blockieren.")
        }
        return out
    }
}
