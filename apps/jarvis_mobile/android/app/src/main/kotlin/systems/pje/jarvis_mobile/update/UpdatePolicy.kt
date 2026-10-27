package systems.pje.jarvis_mobile.update

import org.json.JSONObject

/** One release as the core describes it (`/api/mobile/app-update/latest` › release). */
data class ReleaseInfo(
    val applicationId: String,
    val versionCode: Long,
    val versionName: String,
    val sha256: String,
    val size: Long,
    val signer: String,
    val downloadPath: String,
    val withdrawn: Boolean,
    val changelog: List<String>,
) {
    companion object {
        fun fromJson(j: JSONObject): ReleaseInfo {
            val log = j.optJSONArray("changelog")
            return ReleaseInfo(
                applicationId = j.optString("application_id"),
                versionCode = j.optLong("version_code", -1),
                versionName = j.optString("version_name"),
                sha256 = j.optString("sha256").lowercase(),
                size = j.optLong("size", -1),
                signer = UpdatePolicy.normalizeFingerprint(j.optString("signing_cert_sha256")),
                downloadPath = j.optString("download_path"),
                withdrawn = j.optBoolean("withdrawn"),
                changelog = (0 until (log?.length() ?: 0)).mapNotNull { log!!.optJSONObject(it)?.optString("subject")?.takeIf { s -> s.isNotBlank() } },
            )
        }
    }
}

/**
 * The decisions of the updater without Android in the way (unit-tested): which release is offered, how a broken
 * download resumes, when a failed attempt is retried and when an update may install without asking.
 */
object UpdatePolicy {
    const val DOWNLOAD_PREFIX = "/api/mobile/app-update/releases/"
    const val CHECK_INTERVAL_MS = 30 * 60_000L // automatic checks (app start / foreground); push and job ignore it
    private const val BACKOFF_BASE_MS = 5 * 60_000L
    private const val BACKOFF_MAX_MS = 6 * 3600_000L
    private val SHA256 = Regex("^[0-9a-f]{64}$")

    fun normalizeFingerprint(fp: String?): String = (fp ?: "").replace(":", "").replace(" ", "").trim().lowercase()

    /**
     * Null if [rel] may be installed over the running app; else the reason why not (shown in the app and on the Mac).
     * Android itself would refuse a lower versionCode or another signature – checking first saves the download.
     */
    fun rejectReason(rel: ReleaseInfo, packageName: String, installedCode: Long, installedSigner: String?): String? = when {
        rel.applicationId != packageName ->
            "Diese App-Variante ($packageName) bekommt keine Updates – nur die Release-App ${rel.applicationId}"
        rel.withdrawn -> "Release ${rel.versionCode} wurde zurückgezogen"
        rel.versionCode <= installedCode -> "Version $installedCode ist aktuell"
        !SHA256.matches(rel.sha256) || rel.size <= 0 -> "Release-Angaben unvollständig (Prüfsumme/Größe)"
        !rel.downloadPath.startsWith(DOWNLOAD_PREFIX) -> "Ungültiger Download-Pfad"
        installedSigner.isNullOrBlank() -> "Signatur der installierten App nicht lesbar"
        normalizeFingerprint(installedSigner) != rel.signer ->
            "Die installierte App hat einen anderen Signaturschlüssel – einmal über den Installationslink vom Mac neu installieren"
        else -> null
    }

    /** Bytes of an interrupted download that may be continued; 0 = start again. */
    fun resumeFrom(partial: Long, expected: Long): Long = if (partial in 1 until expected) partial else 0

    /** Start offset of a `Content-Range: bytes <start>-<end>/<total>` answer (206), null if unreadable. */
    fun contentRangeStart(header: String?): Long? =
        Regex("^bytes (\\d+)-\\d+/(\\d+|\\*)$").find(header?.trim() ?: "")?.groupValues?.get(1)?.toLongOrNull()

    /** Wait after [attempts] failed attempts: 5 min, 10 min, 20 min … at most 6 h. */
    fun backoffMs(attempts: Int): Long {
        if (attempts <= 0) return 0
        val shift = (attempts - 1).coerceAtMost(12)
        return (BACKOFF_BASE_MS shl shift).coerceAtMost(BACKOFF_MAX_MS)
    }

    /** Automatic checks at most every [CHECK_INTERVAL_MS]; manual ones (button, push) always. */
    fun checkDue(lastCheckMs: Long, nowMs: Long, manual: Boolean): Boolean =
        manual || lastCheckMs <= 0 || nowMs - lastCheckMs >= CHECK_INTERVAL_MS || nowMs < lastCheckMs

    /**
     * Without being asked only while Sir is not using the app (the update ends the running process) and never during
     * a call. Pressing "Jetzt installieren" overrides the first rule, not the second.
     */
    fun mayInstall(userInitiated: Boolean, foreground: Boolean, callActive: Boolean): Boolean =
        !callActive && (userInitiated || !foreground)

    /** Android 12+ installs an update of the installing app itself without a confirmation dialog. */
    fun unattendedPossible(sdk: Int): Boolean = sdk >= 31

    /** Install results after which the downloaded file is useless (a retry would fail the same way). */
    fun discardAfter(status: Int): Boolean = status in setOf(
        4, // STATUS_FAILURE_INVALID
        5, // STATUS_FAILURE_CONFLICT
        7, // STATUS_FAILURE_INCOMPATIBLE
    )
}
