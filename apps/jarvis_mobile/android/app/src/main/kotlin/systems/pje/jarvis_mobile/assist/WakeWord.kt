package systems.pje.jarvis_mobile.assist

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.role.RoleManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.service.voice.VoiceInteractionService
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONObject
import systems.pje.jarvis_mobile.MainActivity
import systems.pje.jarvis_mobile.R
import systems.pje.jarvis_mobile.secure.SecureStore
import java.io.File

private const val TAG = "JarvisWake"

/**
 * Settings and permissions of the "Hey Jarvis" wake word. The Picovoice AccessKey lives in its own encrypted
 * store, so logging out of Jarvis does not delete it.
 */
object WakeWord {
    const val CHANNEL_SERVICE = "jarvis_wake"
    const val CHANNEL_ASSIST = "jarvis_assist"
    private const val NID_ASSIST = 4712
    private const val NID_RESUME = 4713

    private fun prefs(c: Context) = c.getSharedPreferences("jarvis_wake", Context.MODE_PRIVATE)
    private fun secure(c: Context) = SecureStore(c.applicationContext, "jarvis_wake_secure")

    fun enabled(c: Context) = prefs(c).getBoolean("enabled", false)
    fun screenOnly(c: Context) = prefs(c).getBoolean("screen_only", true)
    fun sensitivity(c: Context) = prefs(c).getFloat("sensitivity", 0.6f)
    fun accessKey(c: Context): String? = secure(c).get("access_key")?.takeIf { it.isNotBlank() }

    fun hasMic(c: Context) = ContextCompat.checkSelfPermission(c, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    fun canOverlay(c: Context) = Settings.canDrawOverlays(c)

    fun isDefaultAssistant(c: Context): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val rm = c.getSystemService(RoleManager::class.java)
            if (rm != null && rm.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) return rm.isRoleHeld(RoleManager.ROLE_ASSISTANT)
        }
        return VoiceInteractionService.isActiveService(c, ComponentName(c, JarvisVoiceInteractionService::class.java))
    }

    /** Optional own keyword (`assets/wakeword/<name>.ppn`, e.g. "Hey Jarvis" from the Picovoice Console) + language model (`<name>.pv`). */
    fun customFile(c: Context, suffix: String): String? = runCatching {
        val name = c.assets.list("wakeword")?.firstOrNull { it.endsWith(suffix) } ?: return null
        val out = File(c.noBackupFilesDir, name)
        c.assets.open("wakeword/$name").use { input -> out.outputStream().use { input.copyTo(it) } }
        out.absolutePath
    }.getOrNull()

    fun configure(c: Context, enabled: Boolean?, screenOnly: Boolean?, accessKey: String?) {
        if (accessKey != null) secure(c).put("access_key", accessKey.trim().ifBlank { null })
        prefs(c).edit().apply {
            if (enabled != null) putBoolean("enabled", enabled)
            if (screenOnly != null) putBoolean("screen_only", screenOnly)
        }.apply()
        when {
            !enabled(c) -> WakeWordService.stop(c)
            WakeWordService.state.value.running -> WakeWordService.refresh(c, rebuild = accessKey != null)
            else -> ensureRunning(c)
        }
    }

    /** Called from the foreground (app opened): Android only grants background microphone access to a service started then. */
    fun ensureRunning(c: Context) {
        if (enabled(c) && hasMic(c) && accessKey(c) != null && !WakeWordService.state.value.running) WakeWordService.start(c)
    }

    fun status(c: Context): String {
        val s = WakeWordService.state.value
        val custom = c.assets.list("wakeword")?.any { it.endsWith(".ppn") } == true
        return JSONObject()
            .put("enabled", enabled(c))
            .put("running", s.running)
            .put("listening", s.listening)
            .put("reason", s.reason)
            .put("error", s.error)
            .put("has_key", accessKey(c) != null)
            .put("screen_only", screenOnly(c))
            .put("keyword", if (custom) "eigenes Schlüsselwort (assets/wakeword)" else "„Jarvis“ (eingebaut, erkennt auch „Hey Jarvis“)")
            .put("mic", hasMic(c))
            .put("notifications", NotificationManagerCompat.from(c).areNotificationsEnabled())
            .put("overlay", canOverlay(c))
            .put("assistant", isDefaultAssistant(c))
            .toString()
    }

    fun createChannels(c: Context) {
        val nm = c.getSystemService(NotificationManager::class.java)
        val service = NotificationChannel(CHANNEL_SERVICE, "„Hey Jarvis“-Erkennung", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Dauerhafter Hinweis, solange Jarvis auf das Aktivierungswort hört"
            setSound(null, null); enableVibration(false); setShowBadge(false)
        }
        val assist = NotificationChannel(CHANNEL_ASSIST, "Sprachfenster", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Öffnet das Sprachfenster, wenn Android das direkte Einblenden verhindert"
            setSound(null, null); enableVibration(false)
        }
        nm.createNotificationChannels(listOf(service, assist))
    }

    /** After a reboot Android does not let a background service open the microphone – Sir taps once. */
    @SuppressLint("MissingPermission")
    fun postResumeReminder(c: Context) {
        createChannels(c)
        val open = PendingIntent.getActivity(
            c, NID_RESUME, Intent(c, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        runCatching {
            NotificationManagerCompat.from(c).notify(
                NID_RESUME,
                NotificationCompat.Builder(c, CHANNEL_SERVICE)
                    .setSmallIcon(R.drawable.ic_stat_jarvis).setColor(Color.parseColor("#5CE1FF"))
                    .setContentTitle("„Hey Jarvis“ wieder aktivieren")
                    .setContentText("Nach dem Neustart einmal tippen – Android verlangt das für das Mikrofon.")
                    .setContentIntent(open).setAutoCancel(true).build(),
            )
        }
    }

    fun clearResumeReminder(c: Context) = NotificationManagerCompat.from(c).cancel(NID_RESUME)

    /** Heads-up / full-screen fallback when Android blocks opening the overlay from the background. */
    @SuppressLint("MissingPermission")
    fun postAssistPrompt(c: Context) {
        createChannels(c)
        val pi = PendingIntent.getActivity(
            c, NID_ASSIST, AssistOverlayActivity.intent(c, "notification"),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        runCatching {
            NotificationManagerCompat.from(c).notify(
                NID_ASSIST,
                NotificationCompat.Builder(c, CHANNEL_ASSIST)
                    .setSmallIcon(R.drawable.ic_stat_jarvis).setColor(Color.parseColor("#5CE1FF"))
                    .setContentTitle("Ja, Sir?")
                    .setContentText("Tippen, um mit Jarvis zu sprechen")
                    .setPriority(NotificationCompat.PRIORITY_HIGH)
                    .setCategory(NotificationCompat.CATEGORY_CALL)
                    .setContentIntent(pi).setFullScreenIntent(pi, true)
                    .setAutoCancel(true).setTimeoutAfter(10_000).build(),
            )
        }
    }

    fun clearAssistPrompt(c: Context) = NotificationManagerCompat.from(c).cancel(NID_ASSIST)
}

/** Opens the voice overlay above whatever is on screen. */
object Assist {
    /** True while an overlay / assistant session is showing – the wake word pauses and frees the microphone. */
    val open = MutableStateFlow(false)

    fun launch(c: Context, source: String) {
        // Default assistant: the system shows our session window, no extra permission needed.
        JarvisVoiceInteractionService.active?.let { svc ->
            if (runCatching { svc.showSession(Bundle().apply { putString("source", source) }, 0) }.isSuccess) return
        }
        // From the app itself or with "display over other apps", an activity may be started from the background.
        if (source == "app" || WakeWord.canOverlay(c)) {
            val ok = runCatching { c.startActivity(AssistOverlayActivity.intent(c, source)) }
                .onFailure { Log.w(TAG, "overlay start failed: ${it.message}") }.isSuccess
            if (ok) return
        }
        WakeWord.postAssistPrompt(c)
    }
}
