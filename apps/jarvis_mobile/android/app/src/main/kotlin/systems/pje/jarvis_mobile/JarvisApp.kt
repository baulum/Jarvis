package systems.pje.jarvis_mobile

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import kotlinx.coroutines.tasks.await
import org.json.JSONObject
import systems.pje.jarvis_mobile.net.Gateway
import systems.pje.jarvis_mobile.notify.Channels
import systems.pje.jarvis_mobile.push.PushRegistration
import systems.pje.jarvis_mobile.update.AppUpdater
import systems.pje.jarvis_mobile.update.UpdateJob

/**
 * Firebase is initialised at runtime from the client options the paired core hands out (`/api/mobile/me`),
 * so the APK does not embed a google-services.json and works with Sir's own Firebase project.
 */
class JarvisApp : Application() {
    var firebaseReady = false
        private set

    override fun onCreate() {
        super.onCreate()
        Channels.create(this)
        Gateway.get(this).store.get("firebase_options")?.let { initFirebase(it) }
        trackForeground()
        // app updates: report a finished update, look for new ones every few hours (docs/MOBILE.md › App-Updates)
        runCatching { AppUpdater.finishAfterUpdate(this) }.onFailure { Log.w("JarvisApp", "Update state: ${it.message}") }
        UpdateJob.schedule(this)
    }

    /** Is any of our activities visible? An update only installs on its own while none is (it ends the app). */
    private fun trackForeground() {
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            private var started = 0
            override fun onActivityStarted(activity: Activity) {
                if (started++ == 0) AppUpdater.onForegroundChanged(this@JarvisApp, true)
            }
            override fun onActivityStopped(activity: Activity) {
                started = (started - 1).coerceAtLeast(0)
                if (started == 0 && !activity.isChangingConfigurations) AppUpdater.onForegroundChanged(this@JarvisApp, false)
            }
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) = Unit
            override fun onActivityResumed(activity: Activity) = Unit
            override fun onActivityPaused(activity: Activity) = Unit
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
            override fun onActivityDestroyed(activity: Activity) = Unit
        })
    }

    private fun initFirebase(json: String): Boolean {
        if (firebaseReady) return true
        return runCatching {
            val o = JSONObject(json)
            val options = FirebaseOptions.Builder()
                .setApiKey(o.getString("apiKey"))
                .setApplicationId(o.getString("appId"))
                .setProjectId(o.getString("projectId"))
                .setGcmSenderId(o.getString("messagingSenderId"))
                .build()
            if (FirebaseApp.getApps(this).isEmpty()) FirebaseApp.initializeApp(this, options)
            firebaseReady = true
            true
        }.getOrElse { Log.w("JarvisApp", "Firebase init failed: ${it.message}"); false }
    }

    /** Returns a JSON status: {ready, token_uploaded, error}. */
    suspend fun configureFirebase(optionsJson: String?): String {
        val gw = Gateway.get(this)
        if (optionsJson.isNullOrBlank()) return JSONObject().put("ready", false).put("error", "Firebase im Core nicht eingerichtet").toString()
        if (gw.store.get("firebase_options") != optionsJson && firebaseReady) {
            return JSONObject().put("ready", true).put("error", "Neue Firebase-Konfiguration – App neu starten").toString()
        }
        gw.store.put("firebase_options", optionsJson)
        if (!initFirebase(optionsJson)) return JSONObject().put("ready", false).put("error", "Firebase-Initialisierung fehlgeschlagen").toString()
        return try {
            val token = FirebaseMessaging.getInstance().token.await()
            gw.store.put("fcm_token", token)
            val ok = gw.store.get("fcm_token_uploaded") == token || PushRegistration.upload(this, token)
            JSONObject().put("ready", true).put("token_uploaded", ok).toString()
        } catch (e: Exception) {
            JSONObject().put("ready", true).put("token_uploaded", false).put("error", e.message).toString()
        }
    }
}
