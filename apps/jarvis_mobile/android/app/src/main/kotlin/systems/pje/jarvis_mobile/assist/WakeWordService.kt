package systems.pje.jarvis_mobile.assist

import ai.picovoice.porcupine.Porcupine
import ai.picovoice.porcupine.PorcupineActivationException
import ai.picovoice.porcupine.PorcupineActivationLimitException
import ai.picovoice.porcupine.PorcupineActivationRefusedException
import ai.picovoice.porcupine.PorcupineActivationThrottledException
import ai.picovoice.porcupine.PorcupineException
import ai.picovoice.porcupine.PorcupineInvalidArgumentException
import ai.picovoice.porcupine.PorcupineManager
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch
import systems.pje.jarvis_mobile.R
import systems.pje.jarvis_mobile.call.CallManager

private const val TAG = "JarvisWake"

data class WakeState(
    val running: Boolean = false,
    val listening: Boolean = false,
    val reason: String = "aus",
    val error: String? = null,
)

/**
 * Listens for the wake word on the device (Picovoice Porcupine – a few % of one core, no audio leaves the phone).
 * Foreground service of type microphone with a quiet ongoing notification, as Android requires.
 * Pauses (and releases the microphone) during Jarvis calls, while the voice overlay is open, when Sir pauses it
 * from the notification and – by default, to save battery – while the screen is off.
 */
class WakeWordService : Service() {
    companion object {
        private const val ACTION_PAUSE = "systems.pje.jarvis.wake.PAUSE"
        private const val ACTION_RESUME = "systems.pje.jarvis.wake.RESUME"
        private const val ACTION_STOP = "systems.pje.jarvis.wake.STOP"
        private const val ACTION_REFRESH = "systems.pje.jarvis.wake.REFRESH"
        private const val EXTRA_REBUILD = "rebuild"
        private const val NOTIFICATION_ID = 4711

        val state = MutableStateFlow(WakeState())

        fun start(c: Context) {
            runCatching { ContextCompat.startForegroundService(c, Intent(c, WakeWordService::class.java)) }
                .onFailure { state.value = WakeState(error = "Android hat den Start verweigert – App öffnen und erneut aktivieren.") }
        }

        /** Only while running (then the app counts as foreground and a plain start is allowed). */
        fun refresh(c: Context, rebuild: Boolean) {
            runCatching { c.startService(Intent(c, WakeWordService::class.java).setAction(ACTION_REFRESH).putExtra(EXTRA_REBUILD, rebuild)) }
        }

        fun stop(c: Context) {
            c.stopService(Intent(c, WakeWordService::class.java))
        }
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val main = Handler(Looper.getMainLooper())
    private val screenOn = MutableStateFlow(true)
    private val screenOnly = MutableStateFlow(true)
    private val userPaused = MutableStateFlow(false)
    private val rebuilds = MutableStateFlow(0)
    private var porcupine: PorcupineManager? = null
    private var listening = false
    private var pauseReason: String? = "startet"
    private var error: String? = null
    private var receiverRegistered = false

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            screenOn.value = intent.action != Intent.ACTION_SCREEN_OFF
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        WakeWord.createChannels(this)
        WakeWord.clearResumeReminder(this)
        if (!startInForeground()) {
            stopSelf(); return
        }
        screenOn.value = getSystemService(PowerManager::class.java).isInteractive
        screenOnly.value = WakeWord.screenOnly(this)
        ContextCompat.registerReceiver(
            this, screenReceiver,
            IntentFilter().apply { addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF) },
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        receiverRegistered = true
        scope.launch {
            combine(CallManager.state, Assist.open, screenOn, screenOnly, userPaused) { call, open, screen, onlyScreen, paused ->
                when {
                    paused -> "pausiert"
                    call.status !in listOf("idle", "ended") -> "Anruf läuft"
                    open -> "Sprachfenster offen"
                    onlyScreen && !screen -> "Bildschirm aus"
                    else -> null
                }
            }.combine(rebuilds) { why, _ -> why }.collect { update(it) }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PAUSE -> userPaused.value = true
            ACTION_RESUME -> userPaused.value = false
            ACTION_STOP -> {
                WakeWord.configure(this, enabled = false, screenOnly = null, accessKey = null); return START_NOT_STICKY
            }
            ACTION_REFRESH -> {
                screenOnly.value = WakeWord.screenOnly(this)
                if (intent?.getBooleanExtra(EXTRA_REBUILD, false) == true) {
                    release(); error = null; rebuilds.value++
                }
            }
        }
        return START_STICKY
    }

    private fun startInForeground(): Boolean = try {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(), type)
        state.value = WakeState(running = true, reason = "startet")
        true
    } catch (e: Exception) {
        Log.w(TAG, "startForeground failed: ${e.message}")
        state.value = WakeState(error = "Android erlaubt den Mikrofondienst gerade nicht – App öffnen und erneut aktivieren.")
        false
    }

    // ------------------------------------------------------------------ listening
    private fun update(why: String?) {
        pauseReason = why
        if (why == null && error == null) listen() else mute()
        publish()
    }

    private fun listen() {
        if (listening) return
        val pm = porcupine ?: build() ?: return
        try {
            pm.start(); listening = true
        } catch (e: Exception) {
            fail(describe(e))
        }
    }

    private fun mute() {
        if (!listening) return
        listening = false
        runCatching { porcupine?.stop() }
    }

    private fun build(): PorcupineManager? {
        val key = WakeWord.accessKey(this) ?: run { fail("Picovoice-AccessKey fehlt"); return null }
        return try {
            val b = PorcupineManager.Builder().setAccessKey(key).setSensitivity(WakeWord.sensitivity(this))
            val keyword = WakeWord.customFile(this, ".ppn")
            if (keyword != null) {
                b.setKeywordPath(keyword)
                WakeWord.customFile(this, ".pv")?.let { b.setModelPath(it) }
            } else {
                b.setKeyword(Porcupine.BuiltInKeyword.JARVIS)
            }
            b.setErrorCallback { e -> main.post { fail(describe(e)) } }
            b.build(applicationContext) { main.post { onWake() } }.also { porcupine = it }
        } catch (e: Exception) {
            fail(describe(e)); null
        }
    }

    private fun onWake() {
        if (!listening) return
        Log.i(TAG, "wake word detected")
        mute() // free the microphone for the overlay
        publish()
        Assist.launch(this, "wake")
        // Overlay blocked or dismissed before it opened → listen again instead of staying deaf.
        main.postDelayed({ if (!Assist.open.value) update(pauseReason) }, 5_000)
    }

    private fun fail(message: String) {
        Log.w(TAG, "wake word: $message")
        error = message
        mute(); release()
        publish()
    }

    private fun describe(e: Throwable): String = when (e) {
        is PorcupineActivationLimitException -> "AccessKey: Gerätelimit erreicht (Picovoice Console prüfen)"
        is PorcupineActivationThrottledException -> "AccessKey: zu viele Aktivierungen – später erneut versuchen"
        is PorcupineActivationRefusedException -> "AccessKey abgelehnt"
        is PorcupineActivationException -> "AccessKey ungültig oder keine Internetverbindung zur Aktivierung"
        is PorcupineInvalidArgumentException -> "AccessKey oder Schlüsselwortdatei ungültig"
        is PorcupineException -> "Wake-Word-Fehler: ${e.message}"
        else -> "Mikrofon nicht verfügbar: ${e.message}"
    }

    private fun release() {
        listening = false
        porcupine?.let { runCatching { it.stop() }; runCatching { it.delete() } }
        porcupine = null
    }

    // ------------------------------------------------------------------ status + notification
    private fun publish() {
        state.value = WakeState(
            running = true, listening = listening, error = error,
            reason = error ?: pauseReason ?: if (listening) "hört zu" else "wartet",
        )
        runCatching { NotificationManagerCompat.from(this).notify(NOTIFICATION_ID, notification()) }
    }

    private fun notification(): Notification {
        val open = PendingIntent.getActivity(
            this, 1, AssistOverlayActivity.intent(this, "notification"),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        fun action(action: String, rc: Int) = PendingIntent.getService(
            this, rc, Intent(this, WakeWordService::class.java).setAction(action),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val paused = userPaused.value
        val title = when {
            error != null -> "„Hey Jarvis“ – Fehler"
            listening -> "„Hey Jarvis“ ist aktiv"
            else -> "„Hey Jarvis“ pausiert"
        }
        val text = error ?: if (listening) "Erkennung nur auf dem Gerät · tippen, um direkt zu sprechen" else (pauseReason ?: "wartet")
        return NotificationCompat.Builder(this, WakeWord.CHANNEL_SERVICE)
            .setSmallIcon(R.drawable.ic_stat_jarvis)
            .setColor(Color.parseColor(if (error != null) "#FFB547" else "#5CE1FF"))
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(0, if (paused) "Fortsetzen" else "Pausieren", action(if (paused) ACTION_RESUME else ACTION_PAUSE, 2))
            .addAction(0, "Ausschalten", action(ACTION_STOP, 3))
            .build()
    }

    override fun onDestroy() {
        scope.cancel()
        main.removeCallbacksAndMessages(null)
        if (receiverRegistered) runCatching { unregisterReceiver(screenReceiver) }
        release()
        if (state.value.running) state.value = WakeState(error = error) // else keep the start failure for the settings screen
        super.onDestroy()
    }
}
