package systems.pje.jarvis_mobile.assist

import ai.picovoice.porcupine.Porcupine
import ai.picovoice.porcupine.PorcupineActivationException
import ai.picovoice.porcupine.PorcupineActivationLimitException
import ai.picovoice.porcupine.PorcupineActivationRefusedException
import ai.picovoice.porcupine.PorcupineActivationThrottledException
import ai.picovoice.porcupine.PorcupineException
import ai.picovoice.porcupine.PorcupineInvalidArgumentException
import ai.picovoice.porcupine.PorcupineKeyException
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
    /** Epoch millis of the last detection (0 = none since the app process started). */
    val lastWake: Long = 0,
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
                .onFailure {
                    Log.w(TAG, "startForegroundService refused: ${it.javaClass.simpleName}: ${it.message}")
                    state.value = WakeState(error = "Android hat den Start verweigert – App öffnen und erneut aktivieren.")
                }
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
    private var destroyed = false
    /** Bumped on release(), so a build still running in the background is discarded. */
    private var buildGen = 0
    private var building = false
    private var retries = 0
    private var lastWake = state.value.lastWake
    private val retry = Runnable {
        Log.i(TAG, "retrying after error")
        error = null; rebuilds.value++
    }

    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            screenOn.value = intent.action != Intent.ACTION_SCREEN_OFF
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "service created")
        WakeWord.createChannels(this)
        WakeWord.clearResumeReminder(this)
        if (!startInForeground()) {
            // e.g. START_STICKY restart from the background on Android 14+: tell Sir instead of failing silently
            if (WakeWord.enabled(this)) WakeWord.postResumeReminder(this)
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
                    Log.i(TAG, "rebuilding with new AccessKey")
                    main.removeCallbacks(retry); retries = 0
                    release(); error = null; rebuilds.value++
                }
            }
        }
        return START_STICKY
    }

    private fun startInForeground(): Boolean = try {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(), type)
        Log.i(TAG, "foreground service started (type microphone)")
        state.value = WakeState(running = true, reason = "startet", lastWake = state.value.lastWake)
        true
    } catch (e: Exception) {
        Log.w(TAG, "startForeground failed: ${e.javaClass.simpleName}: ${e.message}")
        state.value = WakeState(error = "Android erlaubt den Mikrofondienst gerade nicht – App öffnen und erneut aktivieren.")
        false
    }

    // ------------------------------------------------------------------ listening
    private fun update(why: String?) {
        if (why != pauseReason) Log.i(TAG, "state: ${why ?: "listen"}")
        pauseReason = why
        if (why == null && error == null) listen() else mute()
        publish()
    }

    private fun listen() {
        if (listening || building) return
        val pm = porcupine ?: return buildAsync()
        try {
            pm.start(); listening = true; retries = 0
            Log.i(TAG, "listening")
        } catch (e: Exception) {
            fail(e)
        }
    }

    private fun mute() {
        if (!listening) return
        listening = false
        runCatching { porcupine?.stop() }
        Log.i(TAG, "microphone released")
    }

    /** Porcupine activates the AccessKey online and copies model files – off the main thread. */
    private fun buildAsync() {
        val key = WakeWord.accessKey(this) ?: return fail("Picovoice-AccessKey fehlt – in den Einstellungen eintragen", retry = false)
        val gen = ++buildGen
        building = true
        publish()
        scope.launch(Dispatchers.IO) {
            val result = runCatching { build(key) }
            main.post {
                if (destroyed || gen != buildGen) {
                    result.getOrNull()?.let { runCatching { it.delete() } }; return@post
                }
                building = false
                result.onSuccess {
                    porcupine = it
                    update(pauseReason)
                }.onFailure { fail(it) }
            }
        }
    }

    private fun build(key: String): PorcupineManager {
        val b = PorcupineManager.Builder().setAccessKey(key).setSensitivity(WakeWord.sensitivity(this))
        val keyword = WakeWord.customFile(this, ".ppn")
        if (keyword != null) {
            b.setKeywordPath(keyword)
            WakeWord.customFile(this, ".pv")?.let { b.setModelPath(it) }
            Log.i(TAG, "building Porcupine with custom keyword ${keyword.substringAfterLast('/')}")
        } else {
            b.setKeyword(Porcupine.BuiltInKeyword.JARVIS)
            Log.i(TAG, "building Porcupine with built-in keyword JARVIS")
        }
        b.setErrorCallback { e -> main.post { fail(e) } }
        return b.build(applicationContext) { main.post { onWake() } }
    }

    private fun onWake() {
        if (!listening) return
        Log.i(TAG, "wake word detected")
        lastWake = System.currentTimeMillis()
        mute() // free the microphone for the overlay
        publish()
        Assist.launch(this, "wake")
        // Overlay blocked or dismissed before it opened → listen again instead of staying deaf.
        main.postDelayed({ if (!Assist.open.value) update(pauseReason) }, 5_000)
    }

    private fun fail(e: Throwable) {
        fail(describe(e), retry = retryable(e), cause = e)
    }

    /**
     * Shows the reason in the settings and the notification. Transient problems (no network for the activation,
     * microphone busy) are retried with backoff instead of leaving the wake word deaf until Sir toggles it.
     */
    private fun fail(message: String, retry: Boolean, cause: Throwable? = null) {
        Log.w(TAG, "wake word error: $message" + (cause?.let { " (${it.javaClass.simpleName}: ${secret(it.message)})" } ?: ""))
        error = message
        mute(); release()
        main.removeCallbacks(this.retry)
        if (retry) {
            val delay = (30_000L shl retries.coerceAtMost(5)).coerceAtMost(15 * 60_000L)
            retries++
            Log.i(TAG, "retry in ${delay / 1000}s")
            main.postDelayed(this.retry, delay)
        }
        publish()
    }

    private fun retryable(e: Throwable) = when (e) {
        is PorcupineActivationLimitException, is PorcupineActivationRefusedException,
        is PorcupineInvalidArgumentException, is PorcupineKeyException -> false
        else -> true
    }

    /** Porcupine messages must never carry the AccessKey into logcat or the UI. */
    private fun secret(message: String?): String {
        val key = WakeWord.accessKey(this)
        return message.orEmpty().let { if (key != null) it.replace(key, "***") else it }
    }

    private fun describe(e: Throwable): String = when (e) {
        is PorcupineActivationLimitException -> "AccessKey: Gerätelimit erreicht (Picovoice Console prüfen)"
        is PorcupineActivationThrottledException -> "AccessKey: zu viele Aktivierungen – neuer Versuch folgt automatisch"
        is PorcupineActivationRefusedException -> "AccessKey abgelehnt (Picovoice Console prüfen)"
        is PorcupineKeyException -> "AccessKey ungültig – in den Einstellungen neu eintragen"
        is PorcupineActivationException -> "AccessKey-Aktivierung fehlgeschlagen (Internet?) – neuer Versuch folgt automatisch"
        is PorcupineInvalidArgumentException -> "AccessKey oder Schlüsselwortdatei ungültig: ${secret(e.message)}"
        is PorcupineException -> "Wake-Word-Fehler: ${secret(e.message)}"
        else -> "Mikrofon nicht verfügbar: ${secret(e.message)}"
    }

    private fun release() {
        listening = false
        buildGen++; building = false
        porcupine?.let { runCatching { it.stop() }; runCatching { it.delete() } }
        porcupine = null
    }

    // ------------------------------------------------------------------ status + notification
    private fun publish() {
        state.value = WakeState(
            running = true, listening = listening, error = error, lastWake = lastWake,
            reason = error ?: pauseReason ?: when {
                listening -> "hört zu"
                building -> "Erkennung wird geladen"
                else -> "wartet"
            },
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
            building -> "„Hey Jarvis“ startet"
            else -> "„Hey Jarvis“ pausiert"
        }
        val text = error ?: when {
            listening -> "Erkennung nur auf dem Gerät · tippen, um direkt zu sprechen"
            building -> "Erkennung wird geladen …"
            else -> pauseReason ?: "wartet"
        }
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
        Log.i(TAG, "service destroyed")
        destroyed = true
        scope.cancel()
        main.removeCallbacksAndMessages(null)
        if (receiverRegistered) runCatching { unregisterReceiver(screenReceiver) }
        release()
        // else keep the start failure for the settings screen
        if (state.value.running) state.value = WakeState(error = error, lastWake = lastWake)
        super.onDestroy()
    }
}
