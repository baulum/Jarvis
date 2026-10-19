package systems.pje.jarvis_mobile.assist

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.core.view.WindowCompat

/**
 * Translucent voice overlay above the current app – opened by the wake word (needs "display over other apps"
 * or the full-screen notification fallback), the service notification, the app itself and `ACTION_ASSIST`.
 * On the lock screen Sir has to unlock first; nothing is recorded before that.
 */
class AssistOverlayActivity : Activity() {
    companion object {
        private const val EXTRA_SOURCE = "source"

        fun intent(c: Context, source: String): Intent = Intent(c, AssistOverlayActivity::class.java)
            .putExtra(EXTRA_SOURCE, source)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
    }

    private lateinit var panel: AssistPanel
    private var unlocking = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        // shown on the lock screen only to ask for the unlock right here
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED)
        }
        WakeWord.clearAssistPrompt(this)
        panel = AssistPanel(this, object : AssistPanel.Host {
            override fun close() = finish()
            override fun startActivity(intent: Intent) = this@AssistOverlayActivity.startActivity(intent)
        })
        setContentView(panel.root)
        begin()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        WakeWord.clearAssistPrompt(this)
        if (!unlocking) panel.start()
    }

    private fun begin() {
        val km = getSystemService(KeyguardManager::class.java)
        if (!km.isKeyguardLocked) {
            panel.start(); return
        }
        unlocking = true
        panel.message("Bitte entsperren, Sir.")
        km.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
            override fun onDismissSucceeded() {
                unlocking = false; panel.start()
            }
            override fun onDismissCancelled() = finish()
            override fun onDismissError() = finish()
        })
    }

    override fun onStop() {
        super.onStop()
        // Sir switched away: close instead of keeping the microphone in the background.
        if (!isChangingConfigurations && !unlocking) finish()
    }

    override fun onDestroy() {
        panel.release()
        super.onDestroy()
    }
}
