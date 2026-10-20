package systems.pje.jarvis_mobile.assist

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import android.view.View
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import systems.pje.jarvis_mobile.net.ApiException
import systems.pje.jarvis_mobile.net.Gateway

/**
 * Bound by Android while J.A.R.V.I.S. is the default digital assistant (Settings › Apps › Default apps ›
 * Digital assistant). The assist gesture / long-press then opens [JarvisVoiceSession]; the wake word uses
 * [showSession] too, which needs no "display over other apps" permission.
 */
class JarvisVoiceInteractionService : VoiceInteractionService() {
    companion object {
        @Volatile var active: JarvisVoiceInteractionService? = null
            private set
    }

    override fun onReady() {
        super.onReady()
        active = this
    }

    override fun onShutdown() {
        active = null
        super.onShutdown()
    }
}

class JarvisVoiceSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = JarvisVoiceSession(this)
}

/** The assistant overlay itself: same panel as [AssistOverlayActivity], drawn in the system's assistant window. */
class JarvisVoiceSession(context: Context) : VoiceInteractionSession(context) {
    private var panel: AssistPanel? = null

    override fun onCreate() {
        super.onCreate()
        panel = AssistPanel(context, object : AssistPanel.Host {
            override fun close() = hide()
            override fun startActivity(intent: Intent) = context.startActivity(intent)
        })
    }

    override fun onCreateContentView(): View? = panel?.root

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        WakeWord.clearAssistPrompt(context)
        val locked = context.getSystemService(KeyguardManager::class.java).isKeyguardLocked
        if (locked) {
            // Personal answers stay behind the lock screen – Sir unlocks and asks again.
            panel?.message("Bitte zuerst entsperren, Sir.")
        } else {
            panel?.start()
        }
    }

    override fun onHide() {
        panel?.stop()
        super.onHide()
    }

    override fun onDestroy() {
        panel?.release()
        panel = null
        super.onDestroy()
    }
}

/**
 * Speech recognition via Jarvis' Whisper (required for a voice interaction service; also selectable as the
 * system's voice input). Records one utterance and returns the transcript.
 */
class JarvisRecognitionService : RecognitionService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var job: Job? = null
    @Volatile private var stopRequested = false

    override fun onStartListening(recognizerIntent: Intent?, listener: RecognitionService.Callback) {
        job?.cancel()
        stopRequested = false
        job = scope.launch {
            fun safe(block: () -> Unit) = runCatching { block() }
            if (!Gateway.get(this@JarvisRecognitionService).paired) {
                safe { listener.error(SpeechRecognizer.ERROR_CLIENT) }; return@launch
            }
            try {
                safe { listener.readyForSpeech(Bundle()) }
                val wav = withContext(Dispatchers.IO) {
                    VoiceRecorder.record(
                        noSpeechMs = 6_000,
                        active = { isActive },
                        stopEarly = { stopRequested },
                        onSpeech = { safe { listener.beginningOfSpeech() } },
                        onLevel = { l -> safe { listener.rmsChanged(-2f + l * 12f) } },
                    )
                }
                if (wav == null) {
                    safe { listener.error(SpeechRecognizer.ERROR_SPEECH_TIMEOUT) }; return@launch
                }
                safe { listener.endOfSpeech() }
                val text = JarvisVoiceApi.transcribe(this@JarvisRecognitionService, wav)
                if (text.isBlank()) {
                    safe { listener.error(SpeechRecognizer.ERROR_NO_MATCH) }
                } else {
                    safe { listener.results(Bundle().apply { putStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION, arrayListOf(text)) }) }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                safe { listener.error(if (e.code == 0) SpeechRecognizer.ERROR_NETWORK else SpeechRecognizer.ERROR_SERVER) }
            } catch (e: Exception) {
                safe { listener.error(SpeechRecognizer.ERROR_AUDIO) }
            }
        }
    }

    override fun onStopListening(listener: RecognitionService.Callback) {
        stopRequested = true
    }

    override fun onCancel(listener: RecognitionService.Callback) {
        job?.cancel()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}

/** After a reboot or app update: remind Sir to reactivate the wake word (Android forbids starting it silently). */
class WakeBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        if (WakeWord.enabled(context)) WakeWord.postResumeReminder(context)
    }
}
