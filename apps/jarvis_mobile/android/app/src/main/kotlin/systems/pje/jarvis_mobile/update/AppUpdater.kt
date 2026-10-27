package systems.pje.jarvis_mobile.update

import android.annotation.SuppressLint
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.pm.PackageInfoCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import systems.pje.jarvis_mobile.MainActivity
import systems.pje.jarvis_mobile.R
import systems.pje.jarvis_mobile.bridge.NativeEvents
import systems.pje.jarvis_mobile.call.CallManager
import systems.pje.jarvis_mobile.net.ApiException
import systems.pje.jarvis_mobile.net.Gateway
import systems.pje.jarvis_mobile.net.sha256Hex
import systems.pje.jarvis_mobile.notify.Channels
import java.io.File
import java.security.MessageDigest

private const val TAG = "JarvisUpdate"
private const val PREFS = "jarvis_update"
private const val NOTIFICATION_ID = 7301
private const val INSTALL_STALE_MS = 20 * 60_000L

/**
 * OTA updates of the app from the paired Jarvis core (docs/MOBILE.md › App-Updates).
 *
 * check → download (resumable, into no-backup storage) → verify (SHA-256 of the manifest, package, versionCode and
 * the signing certificate of the APK = the one of the running app = the one the core names) → install with Android's
 * PackageInstaller. Android 12+ installs an update of the app itself without a dialog; otherwise, or when the
 * phone's maker insists, Sir confirms once via a notification. An update keeps data, settings, pairing and
 * permissions (same package, same key). Every failure leaves the running app untouched; it is retried with a
 * growing wait, a broken file is downloaded again. The core sees each step (`/api/mobile/app-update/report`).
 *
 * Triggers: app start and return to the foreground ([UpdatePolicy.CHECK_INTERVAL_MS]), push `app_update`, a
 * periodic job ([UpdateJob]) and the button in the settings.
 */
object AppUpdater {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutex = Mutex()
    @Volatile private var bytes = 0L
    @Volatile private var lastReportedTenth = -1
    @Volatile var foreground = false
        private set

    private fun prefs(c: Context) = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private fun dir(c: Context) = File(c.noBackupFilesDir, "updates").apply { mkdirs() }
    private fun apk(c: Context, code: Long) = File(dir(c), "jarvis-$code.apk")
    private fun part(c: Context, code: Long) = File(dir(c), "jarvis-$code.apk.part")

    // ------------------------------------------------------------------ installed app
    fun installedInfo(c: Context): PackageInfo = c.packageManager.getPackageInfo(c.packageName, signingFlags())
    fun installedCode(c: Context): Long = PackageInfoCompat.getLongVersionCode(c.packageManager.getPackageInfo(c.packageName, 0))

    @Suppress("DEPRECATION")
    private fun signingFlags(): Int = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES

    /** SHA-256 of the signing certificate, lowercase hex; null with none or several signers. */
    @Suppress("DEPRECATION")
    fun signerOf(info: PackageInfo?): String? {
        val certs = when {
            info == null -> null
            Build.VERSION.SDK_INT >= 28 -> info.signingInfo?.apkContentsSigners
            else -> info.signatures
        }
        return certs?.singleOrNull()?.let { sha256Hex(it.toByteArray()) }
    }

    fun canInstall(c: Context): Boolean = c.packageManager.canRequestPackageInstalls()

    // ------------------------------------------------------------------ status
    fun status(c: Context): JSONObject {
        val p = prefs(c)
        val info = runCatching { c.packageManager.getPackageInfo(c.packageName, 0) }.getOrNull()
        val target = p.getLong("target_code", 0)
        val size = p.getLong("size", 0)
        val state = p.getString("state", "idle")!!
        val progress = if (state == "downloading" && size > 0) (bytes.toDouble() / size).coerceIn(0.0, 1.0) else null
        return JSONObject()
            .put("state", state)
            .put("version_code", info?.let { PackageInfoCompat.getLongVersionCode(it) })
            .put("version_name", info?.versionName)
            .put("target_code", if (target > 0) target else null)
            .put("target_name", p.getString("target_name", null))
            .put("size", if (size > 0) size else null)
            .put("bytes", if (state == "downloading") bytes else null)
            .put("progress", progress)
            .put("error", p.getString("error", null))
            .put("waiting_for", p.getString("waiting_for", null))
            .put("changelog", JSONArray(p.getString("changelog", "[]")))
            .put("last_check", p.getLong("last_check", 0).takeIf { it > 0 })
            .put("can_install", canInstall(c))
            .put("unattended", UpdatePolicy.unattendedPossible(Build.VERSION.SDK_INT))
            .put("sdk", Build.VERSION.SDK_INT)
    }

    private fun setState(c: Context, state: String, error: String? = null, waitingFor: String? = null, report: Boolean = true) {
        prefs(c).edit().putString("state", state).putString("error", error?.take(300)).putString("waiting_for", waitingFor).commit()
        lastReportedTenth = -1
        emit(c)
        if (report) scope.launch { report(c) }
    }

    private fun emit(c: Context) = NativeEvents.emit("app_update", status(c).toString())

    /** Tells the core where this phone stands (status in the Jarvis UI). Never throws. */
    suspend fun report(c: Context) {
        val gw = Gateway.get(c)
        if (!gw.paired) return
        val s = status(c)
        val body = JSONObject()
            .put("version_code", s.optLong("version_code"))
            .put("version_name", s.optString("version_name").take(80))
            .put("state", s.getString("state"))
            .put("target_code", s.opt("target_code"))
            .put("progress", s.opt("progress"))
            .put("bytes", s.opt("bytes"))
            .put("total", s.opt("size"))
            .put("error", s.opt("error"))
            .put("can_install", s.getBoolean("can_install"))
            .put("unattended", s.getBoolean("unattended"))
            .put("sdk", Build.VERSION.SDK_INT)
        runCatching { gw.request("POST", "/api/mobile/app-update/report", body.toString()) }
            .onFailure { Log.i(TAG, "Report not delivered: ${it.message}") }
    }

    // ------------------------------------------------------------------ triggers
    fun checkAsync(c: Context, manual: Boolean) {
        val app = c.applicationContext
        scope.launch { check(app, manual) }
    }

    /** Push `app_update`: a new release is there – look now, regardless of the last check. */
    fun onPush(c: Context) = checkAsync(c, manual = true)

    /** The app went to the background: a downloaded update may install now. */
    fun onForegroundChanged(c: Context, visible: Boolean) {
        foreground = visible
        if (!visible && prefs(c).getString("state", null) == "ready") {
            val app = c.applicationContext
            scope.launch { mutex.withLock { install(app, userInitiated = false) } }
        }
    }

    /** "Jetzt installieren" in the app. */
    fun installNow(c: Context) {
        val app = c.applicationContext
        scope.launch { mutex.withLock { install(app, userInitiated = true) } }
    }

    // ------------------------------------------------------------------ check + download
    suspend fun check(c: Context, manual: Boolean): JSONObject = mutex.withLock {
        val gw = Gateway.get(c)
        val p = prefs(c)
        val now = System.currentTimeMillis()
        finishAfterUpdate(c)
        if (!gw.paired) return@withLock status(c)
        val state = p.getString("state", "idle")
        if (!manual) {
            if (state == "installing") return@withLock status(c)
            if (state == "waiting_user" && p.getString("waiting_for", null) == "permission" && canInstall(c)) {
                install(c, userInitiated = false) // Sir just allowed it in Android's settings
                return@withLock status(c)
            }
            if (!UpdatePolicy.checkDue(p.getLong("last_check", 0), now, false)) return@withLock status(c)
            if (state == "failed" && now < p.getLong("retry_at", 0)) return@withLock status(c)
        }
        p.edit().putLong("last_check", now).apply()
        val current = installedCode(c)
        val res = try {
            JSONObject(gw.request("GET", "/api/mobile/app-update/latest?current=$current"))
        } catch (e: ApiException) {
            // offline or updates switched off on the core: nothing changes on the phone
            p.edit().putString("error", if (e.code == 503) "App-Updates sind im Jarvis-Core ausgeschaltet" else e.message).apply()
            emit(c)
            return@withLock status(c)
        }
        val relJson = res.optJSONObject("release")
        if (!res.optBoolean("available") || relJson == null) {
            clearFiles(c, keep = null)
            p.edit().remove("target_code").remove("target_name").remove("size").putInt("attempts", 0).apply()
            setState(c, "up_to_date", report = state != "up_to_date" || manual)
            return@withLock status(c)
        }
        val rel = ReleaseInfo.fromJson(relJson)
        UpdatePolicy.rejectReason(rel, c.packageName, current, signerOf(installedInfo(c)))?.let { why ->
            remember(c, rel)
            setState(c, "unsupported", why)
            return@withLock status(c)
        }
        if (p.getLong("target_code", 0) != rel.versionCode) p.edit().putInt("attempts", 0).apply()
        remember(c, rel)
        clearFiles(c, keep = rel.versionCode)
        if (!apk(c, rel.versionCode).isFile && !download(c, gw, rel)) return@withLock status(c)
        val problem = verify(c, apk(c, rel.versionCode), rel)
        if (problem != null) {
            apk(c, rel.versionCode).delete()
            fail(c, problem)
            return@withLock status(c)
        }
        setState(c, "ready")
        install(c, userInitiated = false)
        status(c)
    }

    private fun remember(c: Context, rel: ReleaseInfo) {
        prefs(c).edit()
            .putLong("target_code", rel.versionCode).putString("target_name", rel.versionName)
            .putString("sha256", rel.sha256).putLong("size", rel.size).putString("signer", rel.signer)
            .putString("changelog", JSONArray(rel.changelog.take(10)).toString())
            .apply()
    }

    /** Downloads into the .part file (continuing a broken download); true when complete and checksum-correct. */
    private suspend fun download(c: Context, gw: Gateway, rel: ReleaseInfo): Boolean {
        val part = part(c, rel.versionCode)
        if (part.isFile && UpdatePolicy.resumeFrom(part.length(), rel.size) == 0L) part.delete()
        val free = dir(c).usableSpace
        if (free < rel.size - part.length() + 50_000_000L) {
            fail(c, "Zu wenig Speicher auf dem Handy für das Update (${rel.size / 1_000_000} MB)")
            return false
        }
        bytes = part.length()
        setState(c, "downloading")
        try {
            gw.download(rel.downloadPath, part) { done, _ ->
                bytes = done
                val tenth = (done * 10 / rel.size).toInt()
                if (tenth != lastReportedTenth) {
                    lastReportedTenth = tenth
                    emit(c)
                    scope.launch { report(c) }
                }
            }
        } catch (e: ApiException) {
            if (e.code == 410 || e.code == 404) part.delete() // withdrawn or removed meanwhile: look again next time
            fail(c, e.message ?: "Download fehlgeschlagen")
            return false
        }
        setState(c, "verifying")
        val sum = withContext(Dispatchers.IO) { sha256File(part) }
        if (part.length() != rel.size || sum != rel.sha256) {
            part.delete()
            fail(c, "Prüfsumme der heruntergeladenen App stimmt nicht – wird erneut geladen")
            return false
        }
        part.renameTo(apk(c, rel.versionCode))
        return true
    }

    /** Null if the APK is exactly the announced release of this app, signed with the running app's key. */
    @Suppress("DEPRECATION")
    private fun verify(c: Context, file: File, rel: ReleaseInfo): String? {
        if (file.length() != rel.size || sha256File(file) != rel.sha256) return "Prüfsumme der App stimmt nicht"
        val info = c.packageManager.getPackageArchiveInfo(file.path, signingFlags())
            ?: return "Die heruntergeladene Datei ist keine lesbare App"
        if (info.packageName != c.packageName) return "Falsches Paket ${info.packageName}"
        if (PackageInfoCompat.getLongVersionCode(info) != rel.versionCode) return "Versionsnummer passt nicht zum Release"
        val signer = signerOf(info)
        if (signer == null || signer != rel.signer || signer != signerOf(installedInfo(c))) {
            return "Signatur der heruntergeladenen App passt nicht – nicht installiert"
        }
        return null
    }

    private fun fail(c: Context, message: String) {
        val p = prefs(c)
        val attempts = p.getInt("attempts", 0) + 1
        p.edit().putInt("attempts", attempts).putLong("retry_at", System.currentTimeMillis() + UpdatePolicy.backoffMs(attempts)).apply()
        Log.w(TAG, "Update failed ($attempts): $message")
        setState(c, "failed", message)
    }

    private fun clearFiles(c: Context, keep: Long?) {
        dir(c).listFiles()?.forEach { f ->
            if (keep == null || !f.name.startsWith("jarvis-$keep.apk")) f.delete()
        }
    }

    // ------------------------------------------------------------------ install
    private fun callActive(): Boolean = CallManager.state.value.status !in listOf("idle", "ended")

    private suspend fun install(c: Context, userInitiated: Boolean) {
        val p = prefs(c)
        val code = p.getLong("target_code", 0)
        val file = apk(c, code)
        if (code <= 0 || !file.isFile || p.getString("state", null) !in listOf("ready", "failed", "waiting_user")) return
        if (code <= installedCode(c)) { finishAfterUpdate(c); return }
        if (!UpdatePolicy.mayInstall(userInitiated, foreground, callActive())) {
            setState(c, "ready", waitingFor = if (callActive()) "call" else "background", report = p.getString("waiting_for", null) == null)
            return
        }
        if (!canInstall(c)) {
            setState(c, "waiting_user", "Installation erlauben: Einstellungen › Apps › J.A.R.V.I.S. › Unbekannte Apps installieren", "permission")
            notifyUser(c, "Update ${p.getString("target_name", "")} bereit", "Einmal erlauben, dass J.A.R.V.I.S. sich selbst aktualisiert.", openApp(c))
            return
        }
        val problem = verify(c, file, ReleaseInfo(
            c.packageName, code, p.getString("target_name", "")!!, p.getString("sha256", "")!!, p.getLong("size", 0),
            p.getString("signer", "")!!, UpdatePolicy.DOWNLOAD_PREFIX, false, emptyList(),
        ))
        if (problem != null) { file.delete(); fail(c, problem); return }
        p.edit().putLong("install_started", System.currentTimeMillis()).putLong("from_code", installedCode(c)).apply()
        setState(c, "installing", report = false)
        report(c) // before the commit: the update ends this process
        try {
            withContext(Dispatchers.IO) { commitSession(c, file, code) }
        } catch (e: Exception) {
            fail(c, "Installation konnte nicht starten: ${e.message ?: e.javaClass.simpleName}")
        }
    }

    @SuppressLint("RequestInstallPackagesPolicy")
    private fun commitSession(c: Context, file: File, code: Long) {
        val installer = c.packageManager.packageInstaller
        installer.mySessions.forEach { runCatching { installer.abandonSession(it.sessionId) } } // leftovers of a crash
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(c.packageName)
            setSize(file.length())
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        try {
            installer.openSession(id).use { session ->
                file.inputStream().use { input ->
                    session.openWrite("base.apk", 0, file.length()).use { out ->
                        input.copyTo(out, 256 * 1024)
                        session.fsync(out)
                    }
                }
                val intent = Intent(c, UpdateInstallReceiver::class.java)
                    .setAction(UpdateInstallReceiver.ACTION).putExtra("code", code)
                // the system adds the result extras, so the PendingIntent must be mutable (explicit target)
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                session.commit(PendingIntent.getBroadcast(c, id, intent, flags).intentSender)
            }
        } catch (e: Exception) {
            runCatching { installer.abandonSession(id) }
            throw e
        }
    }

    /** Result of the PackageInstaller session (only for outcomes that leave this process alive). */
    fun onInstallResult(c: Context, status: Int, message: String?, confirm: Intent?) {
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                if (confirm == null) { fail(c, "Android verlangt eine Bestätigung, liefert aber keinen Dialog"); return }
                setState(c, "waiting_user", "Bitte die Installation bestätigen", "confirm")
                val name = prefs(c).getString("target_name", "")
                if (foreground) {
                    runCatching { c.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                        .onFailure { notifyUser(c, "Update $name bereit", "Tippen, um zu installieren.", confirmIntent(c, confirm)) }
                } else {
                    notifyUser(c, "Update $name bereit", "Tippen, um zu installieren – Daten und Kopplung bleiben erhalten.", confirmIntent(c, confirm))
                }
            }
            PackageInstaller.STATUS_SUCCESS -> finishAfterUpdate(c)
            else -> {
                val code = prefs(c).getLong("target_code", 0)
                if (UpdatePolicy.discardAfter(status)) apk(c, code).delete()
                val why = when (status) {
                    PackageInstaller.STATUS_FAILURE_ABORTED -> "Installation abgebrochen"
                    PackageInstaller.STATUS_FAILURE_BLOCKED -> "Installation vom System blockiert"
                    PackageInstaller.STATUS_FAILURE_STORAGE -> "Zu wenig Speicher für die Installation"
                    PackageInstaller.STATUS_FAILURE_CONFLICT -> "Konflikt mit der installierten App (anderer Schlüssel?)"
                    PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> "App passt nicht zu diesem Handy"
                    PackageInstaller.STATUS_FAILURE_INVALID -> "Ungültige App-Datei"
                    else -> "Installation fehlgeschlagen"
                }
                fail(c, if (message.isNullOrBlank()) why else "$why: ${message.take(160)}")
                cancelNotification(c)
            }
        }
    }

    /**
     * After the update (MY_PACKAGE_REPLACED, app start) or a broken install: reports "installed" once the running
     * versionCode reached the target, or "failed" if an install never finished.
     */
    fun finishAfterUpdate(c: Context) {
        val p = prefs(c)
        val target = p.getLong("target_code", 0)
        val state = p.getString("state", "idle")
        if (target <= 0 || state == "up_to_date" || state == "installed") return
        if (installedCode(c) >= target) {
            val name = p.getString("target_name", "")
            clearFiles(c, keep = null)
            p.edit().putInt("attempts", 0).remove("install_started").apply()
            setState(c, "installed")
            cancelNotification(c)
            if (p.getLong("from_code", 0) in 1 until target) {
                notifyUser(c, "J.A.R.V.I.S. aktualisiert", "Version $name ist installiert.", openApp(c))
            }
        } else if (state == "installing" && System.currentTimeMillis() - p.getLong("install_started", 0) > INSTALL_STALE_MS) {
            fail(c, "Installation nicht abgeschlossen – neuer Versuch folgt")
        }
    }

    // ------------------------------------------------------------------ notifications
    private fun openApp(c: Context): PendingIntent = PendingIntent.getActivity(
        c, NOTIFICATION_ID,
        Intent(c, MainActivity::class.java).setAction(Intent.ACTION_VIEW).setData(Uri.parse("jarvis://settings"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun confirmIntent(c: Context, confirm: Intent): PendingIntent = PendingIntent.getActivity(
        c, NOTIFICATION_ID + 1, confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    @SuppressLint("MissingPermission")
    private fun notifyUser(c: Context, title: String, body: String, tap: PendingIntent) {
        val nm = c.getSystemService(NotificationManager::class.java)
        if (!nm.areNotificationsEnabled()) return
        val n = NotificationCompat.Builder(c, Channels.forCategory("system"))
            .setSmallIcon(R.drawable.ic_stat_jarvis)
            .setColor(Color.parseColor("#5CE1FF"))
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        nm.notify(NOTIFICATION_ID, n)
    }

    private fun cancelNotification(c: Context) = c.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)

    private fun sha256File(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { input ->
            val buf = ByteArray(256 * 1024)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
