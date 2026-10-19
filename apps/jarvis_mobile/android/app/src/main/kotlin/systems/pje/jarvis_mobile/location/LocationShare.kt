package systems.pje.jarvis_mobile.location

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.Build
import android.os.CancellationSignal
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import systems.pje.jarvis_mobile.net.Gateway
import kotlin.coroutines.resume

private const val TAG = "JarvisLocation"
private const val KEY = "location_sharing"
private const val DELETE_PENDING = "location_delete_pending"
private const val FIX_TIMEOUT_MS = 10_000L // Jarvis waits ~12 s for an answer to its request

/**
 * Location sharing with the Jarvis core – off unless Sir switches it on in the app. Only then does the phone
 * send its position: when the app comes to the foreground, when sharing is switched on, and when Jarvis asks
 * (push `location_request`). It goes straight to the paired core over the pinned TLS gateway; switching
 * sharing off deletes the server-side copy. Platform LocationManager only (no Play Services), never logged.
 */
object LocationShare {
    fun enabled(context: Context): Boolean = Gateway.get(context).store.get(KEY) == "1"

    /** none | while_in_use | always – "always" is needed to answer Jarvis while the app is closed. */
    fun permission(context: Context): String {
        fun granted(p: String) = ContextCompat.checkSelfPermission(context, p) == PackageManager.PERMISSION_GRANTED
        val foreground = granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION)
        return when {
            !foreground -> "none"
            Build.VERSION.SDK_INT < 29 || granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION) -> "always"
            else -> "while_in_use"
        }
    }

    fun status(context: Context): JSONObject {
        val lm = context.getSystemService(LocationManager::class.java)
        return JSONObject()
            .put("enabled", enabled(context))
            .put("permission", permission(context))
            .put("precise", ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED)
            .put("location_on", LocationManagerCompat.isLocationEnabled(lm))
    }

    suspend fun setEnabled(context: Context, on: Boolean): Boolean {
        val gw = Gateway.get(context)
        gw.store.put(KEY, if (on) "1" else null)
        if (on) return report(context)
        gw.store.put(DELETE_PENDING, "1")
        return deletePending(context)
    }

    /** Forget the copy on the server; retried on the next report() if Jarvis was unreachable. */
    private suspend fun deletePending(context: Context): Boolean {
        val gw = Gateway.get(context)
        if (gw.store.get(DELETE_PENDING) == null || !gw.paired) return true
        return runCatching { gw.request("DELETE", "/api/mobile/location"); gw.store.put(DELETE_PENDING, null); true }
            .getOrElse { Log.w(TAG, "Location delete failed: ${it.message}"); false }
    }

    /** Sends one current fix if sharing is on. Returns false when off, not permitted or no fix available. */
    suspend fun report(context: Context): Boolean {
        val gw = Gateway.get(context)
        if (!enabled(context)) { deletePending(context); return false }
        if (!gw.paired || permission(context) == "none") return false
        val loc = currentLocation(context) ?: return false
        val body = JSONObject()
            .put("latitude", loc.latitude)
            .put("longitude", loc.longitude)
            .put("timestamp_ms", loc.time)
        if (loc.hasAccuracy()) body.put("accuracy_m", loc.accuracy.toDouble())
        return runCatching { gw.request("PUT", "/api/mobile/location", body.toString()); true }
            .getOrElse { Log.w(TAG, "Location upload failed: ${it.message}"); false }
    }

    @SuppressLint("MissingPermission") // checked in report()
    private suspend fun currentLocation(context: Context): Location? {
        val lm = context.getSystemService(LocationManager::class.java)
        if (!LocationManagerCompat.isLocationEnabled(lm)) return null
        val providers = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER).filter { lm.isProviderEnabled(it) }
        val provider = if (Build.VERSION.SDK_INT >= 31 && lm.hasProvider(LocationManager.FUSED_PROVIDER)) LocationManager.FUSED_PROVIDER
        else providers.firstOrNull()
        val fresh = provider?.let { p ->
            withTimeoutOrNull(FIX_TIMEOUT_MS) {
                suspendCancellableCoroutine { cont ->
                    val signal = CancellationSignal()
                    cont.invokeOnCancellation { signal.cancel() }
                    LocationManagerCompat.getCurrentLocation(lm, p, signal, ContextCompat.getMainExecutor(context)) { loc ->
                        if (cont.isActive) cont.resume(loc)
                    }
                }
            }
        }
        // no fresh fix in time: the newest cached one (its real timestamp goes along, so Jarvis sees its age)
        return fresh ?: providers.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }.maxByOrNull { it.time }
    }
}
