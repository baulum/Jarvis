package systems.pje.jarvis_mobile.bridge

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import org.json.JSONObject
import systems.pje.jarvis_mobile.assist.WakeWord
import systems.pje.jarvis_mobile.location.LocationShare
import systems.pje.jarvis_mobile.phone.PhoneControlService
import systems.pje.jarvis_mobile.update.AppUpdater

/**
 * The setup flow after pairing (Flutter `SetupScreen`): the real state of every permission or system setting it
 * offers, read straight from Android – never from a cached answer – so a step shows what is true after Sir comes
 * back from a system page. `null` (JSON null) = not applicable on this Android version.
 */
object Setup {
    private fun prefs(c: Context) = c.getSharedPreferences("jarvis_setup", Context.MODE_PRIVATE)

    /** Sir finished (or postponed) the setup flow; reset by a new pairing so the flow shows again. */
    fun done(c: Context) = prefs(c).getBoolean("done", false)
    fun setDone(c: Context, done: Boolean) = prefs(c).edit().putBoolean("done", done).apply()

    private fun granted(c: Context, p: String) = ContextCompat.checkSelfPermission(c, p) == PackageManager.PERMISSION_GRANTED

    fun status(c: Context): JSONObject {
        val nm = c.getSystemService(NotificationManager::class.java)
        val sdk = Build.VERSION.SDK_INT
        val location = LocationShare.permission(c)
        return JSONObject()
            .put("sdk", sdk)
            .put("notifications", nm.areNotificationsEnabled())
            .put("microphone", WakeWord.hasMic(c))
            .put("bluetooth", if (sdk >= 31) granted(c, Manifest.permission.BLUETOOTH_CONNECT) else JSONObject.NULL)
            .put("fullscreen", if (sdk >= 34) nm.canUseFullScreenIntent() else JSONObject.NULL)
            .put("battery", WakeWord.batteryUnrestricted(c))
            .put("overlay", WakeWord.canOverlay(c))
            .put("assistant", WakeWord.isDefaultAssistant(c))
            .put("accessibility", PhoneControlService.isEnabled(c))
            // the step switches sharing on as well: permission alone sends nothing
            .put("location", LocationShare.enabled(c) && location != "none")
            .put("location_permission", location)
            .put("install", AppUpdater.canInstall(c))
            .put("done", done(c))
    }
}
