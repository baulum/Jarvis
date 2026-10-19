package systems.pje.jarvis_mobile.net

import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.util.Base64
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import systems.pje.jarvis_mobile.secure.DeviceKey
import systems.pje.jarvis_mobile.secure.PushKey
import systems.pje.jarvis_mobile.secure.SecureStore
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

private const val TAG = "JarvisGateway"
private val JSON = "application/json".toMediaType()

fun sha256Hex(data: ByteArray): String =
    MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

fun randomNonce(): String = ByteArray(16).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }

class ApiException(val code: Int, message: String) : Exception(message)

/**
 * Accepts exactly the gateway certificate whose SHA-256 came with the pairing QR code (no CA, no hostname:
 * the pin is stronger and survives changing LAN/Tailscale addresses). Without a pin (gateway behind a real
 * certificate, `tls: external`) the system trust store is used.
 */
class PinnedTrust(private val pin: String?) : X509TrustManager {
    private val system: X509TrustManager = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        .apply { init(null as KeyStore?) }.trustManagers.filterIsInstance<X509TrustManager>().first()

    override fun checkClientTrusted(chain: Array<out X509Certificate>, authType: String) =
        throw CertificateException("client certificates not supported")

    override fun checkServerTrusted(chain: Array<out X509Certificate>, authType: String) {
        if (pin.isNullOrBlank()) return system.checkServerTrusted(chain, authType)
        val actual = sha256Hex(chain.first().encoded)
        if (!MessageDigest.isEqual(actual.toByteArray(), pin.lowercase().toByteArray())) {
            throw CertificateException("gateway certificate does not match the paired pin")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
}

object Http {
    @Volatile private var cached: Pair<String?, OkHttpClient>? = null

    @SuppressLint("CustomX509TrustManager")
    fun client(pin: String?): OkHttpClient {
        cached?.let { if (it.first == pin) return it.second }
        val trust = PinnedTrust(pin)
        val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
        val c = OkHttpClient.Builder()
            .sslSocketFactory(ssl.socketFactory, trust)
            .apply { if (!pin.isNullOrBlank()) hostnameVerifier { _, _ -> true } } // pin replaces hostname check
            .connectTimeout(6, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
        cached = pin to c
        return c
    }
}

/**
 * Pairing, tokens and authenticated requests – the single source of truth for credentials, shared by the
 * Flutter UI (via the bridge), the push service, the call service and notification actions.
 */
class Gateway private constructor(private val context: Context) {
    companion object {
        @Volatile private var instance: Gateway? = null
        fun get(context: Context): Gateway =
            instance ?: synchronized(this) { instance ?: Gateway(context.applicationContext).also { instance = it } }
    }

    val store = SecureStore(context)
    private val mutex = Mutex()
    @Volatile private var access: Pair<String, Long>? = null
    @Volatile var baseUrl: String? = store.get("base_url")
        private set
    var onRevoked: (() -> Unit)? = null

    val paired: Boolean get() = store.get("device_id") != null && store.get("refresh_token") != null
    val deviceId: String? get() = store.get("device_id")
    val pin: String? get() = store.get("pin")
    fun urls(): List<String> = store.get("urls")?.let { j -> JSONArray(j).let { a -> (0 until a.length()).map { a.getString(it) } } } ?: emptyList()

    private fun http() = Http.client(pin)

    // ------------------------------------------------------------------ base URL (LAN / Tailscale / VPS)
    suspend fun resolveBase(force: Boolean = false): String = withContext(Dispatchers.IO) {
        val current = baseUrl
        if (!force && current != null) return@withContext current
        val candidates = (listOfNotNull(current) + urls()).distinct()
        val quick = http().newBuilder().connectTimeout(2500, TimeUnit.MILLISECONDS).readTimeout(3, TimeUnit.SECONDS).build()
        for (u in candidates) {
            val ok = runCatching {
                quick.newCall(Request.Builder().url("$u/api/health").build()).execute().use { it.isSuccessful }
            }.getOrDefault(false)
            if (ok) {
                baseUrl = u; store.put("base_url", u); return@withContext u
            }
        }
        throw ApiException(0, "Jarvis ist nicht erreichbar")
    }

    // ------------------------------------------------------------------ pairing
    suspend fun pair(qrPayload: String, deviceName: String, appVersion: String): JSONObject = withContext(Dispatchers.IO) {
        val qr = JSONObject(qrPayload)
        require(qr.optString("t") == "jarvis-pair") { "Das ist kein J.A.R.V.I.S.-Kopplungscode" }
        val code = qr.getString("code")
        val urls = qr.getJSONArray("urls")
        val pin = qr.optString("fp").takeIf { it.isNotBlank() && it != "null" }
        store.put("pin", pin)
        store.put("urls", urls.toString())
        baseUrl = null
        store.put("base_url", null)
        val base = resolveBase(force = true)
        DeviceKey.ensure(context)
        val ts = System.currentTimeMillis() / 1000
        val nonce = randomNonce()
        val sig = DeviceKey.sign("pair|-|$ts|$nonce|${sha256Hex(code.toByteArray())}".toByteArray())
        val body = JSONObject()
            .put("code", code).put("name", deviceName).put("platform", "android")
            .put("model", "${Build.MANUFACTURER} ${Build.MODEL} (Android ${Build.VERSION.RELEASE})")
            .put("app_version", appVersion).put("public_key", DeviceKey.publicKeySpki())
            .put("ts", ts).put("nonce", nonce).put("signature", sig)
        val res = http().newCall(
            Request.Builder().url("$base/api/mobile/pair").post(body.toString().toRequestBody(JSON)).build(),
        ).execute().use { r ->
            val text = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw ApiException(r.code, detail(text) ?: "Kopplung fehlgeschlagen (${r.code})")
            JSONObject(text)
        }
        PushKey.import(Base64.decode(res.getString("push_key"), Base64.NO_WRAP))
        store.put("device_id", res.getString("device_id"))
        store.put("refresh_token", res.getString("refresh_token"))
        access = res.getString("access_token") to res.getLong("access_expires_at")
        res.remove("push_key"); res.remove("refresh_token"); res.remove("access_token")
        res
    }

    // ------------------------------------------------------------------ tokens
    suspend fun accessToken(): String = mutex.withLock {
        val cached = access
        if (cached != null && cached.second - 60 > System.currentTimeMillis() / 1000) return cached.first
        refreshLocked()
    }

    private suspend fun refreshLocked(): String = withContext(Dispatchers.IO) {
        val device = store.get("device_id") ?: throw ApiException(401, "nicht gekoppelt")
        val refresh = store.get("refresh_token") ?: throw ApiException(401, "nicht gekoppelt")
        val body = JSONObject().put("device_id", device).put("refresh_token", refresh).toString()
        val base = resolveBase()
        val req = signed(Request.Builder().url("$base/api/mobile/token"), "refresh", device, body.toByteArray())
            .post(body.toRequestBody(JSON)).build()
        try {
            http().newCall(req).execute().use { r ->
                val text = r.body?.string().orEmpty()
                if (r.code == 401) {
                    Log.w(TAG, "Refresh rejected: $text"); revokedLocally(); throw ApiException(401, detail(text) ?: "abgemeldet")
                }
                if (!r.isSuccessful) throw ApiException(r.code, detail(text) ?: "Token-Erneuerung fehlgeschlagen")
                val j = JSONObject(text)
                store.put("refresh_token", j.getString("refresh_token"))
                access = j.getString("access_token") to j.getLong("access_expires_at")
                j.getString("access_token")
            }
        } catch (e: java.io.IOException) {
            baseUrl = null // network changed? probe again next time
            throw ApiException(0, "Jarvis ist nicht erreichbar")
        }
    }

    private fun signed(b: Request.Builder, purpose: String, device: String, body: ByteArray): Request.Builder {
        val ts = System.currentTimeMillis() / 1000
        val nonce = randomNonce()
        val sig = DeviceKey.sign("$purpose|$device|$ts|$nonce|${sha256Hex(body)}".toByteArray())
        return b.header("X-Jarvis-Ts", ts.toString()).header("X-Jarvis-Nonce", nonce).header("X-Jarvis-Signature", sig)
    }

    private fun revokedLocally() {
        store.put("refresh_token", null); access = null
        onRevoked?.invoke()
    }

    // ------------------------------------------------------------------ requests
    /**
     * [readTimeoutS] for slow calls (e.g. `/api/chat` with `wait`); such requests are not repeated after a read
     * timeout – the core may already be working on them.
     */
    suspend fun request(method: String, path: String, body: String? = null, signPurpose: String? = null, readTimeoutS: Long? = null): String {
        val rb = if (body != null) body.toRequestBody(JSON) else if (method == "GET" || method == "DELETE") null else "".toRequestBody(JSON)
        return send(method, path, rb, (body ?: "").toByteArray(), signPurpose, readTimeoutS)
    }

    /** Raw POST body (e.g. WAV audio for `/api/voice/stt`). */
    suspend fun upload(path: String, data: ByteArray, contentType: String, readTimeoutS: Long? = null): String =
        send("POST", path, data.toRequestBody(contentType.toMediaType()), data, null, readTimeoutS)

    private suspend fun send(method: String, path: String, rb: RequestBody?, bytes: ByteArray, signPurpose: String?, readTimeoutS: Long?): String =
        withContext(Dispatchers.IO) {
            val client = http().let { c -> if (readTimeoutS != null) c.newBuilder().readTimeout(readTimeoutS, TimeUnit.SECONDS).build() else c }
            var attempt = 0
            while (true) {
                attempt++
                val base = resolveBase(force = attempt > 1 && baseUrl == null)
                var b = Request.Builder().url("$base$path").header("Authorization", "Bearer ${accessToken()}")
                if (signPurpose != null) b = signed(b, signPurpose, deviceId!!, bytes)
                val req = b.method(method, rb).build()
                try {
                    client.newCall(req).execute().use { r ->
                        val text = r.body?.string().orEmpty()
                        if (r.code == 401 && attempt == 1) {
                            access = null; return@use null
                        }
                        if (!r.isSuccessful) throw ApiException(r.code, detail(text) ?: "Fehler ${r.code}")
                        text
                    }?.let { return@withContext it }
                } catch (e: java.io.IOException) {
                    if (readTimeoutS != null && e is java.net.SocketTimeoutException) throw ApiException(0, "Jarvis antwortet nicht rechtzeitig")
                    baseUrl = null
                    if (attempt >= 2) throw ApiException(0, "Jarvis ist nicht erreichbar")
                }
            }
            @Suppress("UNREACHABLE_CODE") ""
        }

    suspend fun logout() {
        runCatching { request("POST", "/api/mobile/logout") }
        forget()
    }

    fun forget() {
        store.clear(); access = null; baseUrl = null
        PushKey.delete(); DeviceKey.delete()
    }

    private fun detail(text: String): String? = runCatching { JSONObject(text).optString("detail").ifBlank { null } }.getOrNull()
}
