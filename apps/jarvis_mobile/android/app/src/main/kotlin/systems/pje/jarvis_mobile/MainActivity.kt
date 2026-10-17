package systems.pje.jarvis_mobile

import android.content.Intent
import android.os.Bundle
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import org.json.JSONObject
import systems.pje.jarvis_mobile.bridge.NativeBridge
import systems.pje.jarvis_mobile.bridge.NativeEvents
import systems.pje.jarvis_mobile.call.CallManager

/** FlutterFragmentActivity: required by local_auth (BiometricPrompt). Shows over the lock screen only during calls. */
class MainActivity : FlutterFragmentActivity() {
    companion object {
        const val ACTION_ANSWER = "systems.pje.jarvis.ANSWER"
        const val ACTION_SHOW_CALL = "systems.pje.jarvis.SHOW_CALL"
    }

    private var callWatch: Job? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        NativeBridge(applicationContext, flutterEngine.dartExecutor.binaryMessenger)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handle(intent)
        callWatch = CoroutineScope(Dispatchers.Main).launch {
            CallManager.state.collect { s ->
                val inCall = s.status !in listOf("idle", "ended")
                setShowWhenLocked(inCall)
                setTurnScreenOn(s.status == "ringing")
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    private fun handle(intent: Intent?) {
        when (intent?.action) {
            ACTION_ANSWER -> {
                CallManager.answer(this); NativeEvents.emit("link", JSONObject.quote("jarvis://call"))
            }
            ACTION_SHOW_CALL -> NativeEvents.emit("link", JSONObject.quote("jarvis://call"))
            Intent.ACTION_VIEW -> intent.data?.let { NativeEvents.emit("link", JSONObject.quote(it.toString())) }
        }
    }

    override fun onDestroy() {
        callWatch?.cancel()
        super.onDestroy()
    }
}
