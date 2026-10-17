package systems.pje.jarvis_mobile

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.pje.jarvis_mobile.call.CallState
import systems.pje.jarvis_mobile.call.Endpoint
import systems.pje.jarvis_mobile.net.PinnedTrust
import systems.pje.jarvis_mobile.net.randomNonce
import systems.pje.jarvis_mobile.net.sha256Hex
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

class NativeSecurityTest {
    private fun cert(name: String): X509Certificate =
        CertificateFactory.getInstance("X.509").generateCertificate(javaClass.classLoader!!.getResourceAsStream("$name.pem")) as X509Certificate

    @Test
    fun pinnedCertificateIsAcceptedOthersRejected() {
        val pinned = cert("pinned")
        val trust = PinnedTrust(sha256Hex(pinned.encoded))
        trust.checkServerTrusted(arrayOf(pinned), "ECDHE_ECDSA") // no exception
        assertThrows(CertificateException::class.java) { trust.checkServerTrusted(arrayOf(cert("other")), "ECDHE_ECDSA") }
    }

    @Test
    fun pinComparisonIsCaseInsensitive() {
        val pinned = cert("pinned")
        PinnedTrust(sha256Hex(pinned.encoded).uppercase()).checkServerTrusted(arrayOf(pinned), "ECDHE_ECDSA")
    }

    @Test
    fun sha256MatchesPythonCore() {
        // same input/digest as jarvis.mobile.tokens.sha256_hex("abc")
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", sha256Hex("abc".toByteArray()))
    }

    @Test
    fun noncesAreLongAndUnique() {
        val a = randomNonce()
        assertEquals(32, a.length) // the core rejects nonces shorter than 16
        assertNotEquals(a, randomNonce())
    }

    @Test
    fun callStateSerialisesForFlutter() {
        val s = CallState(
            callId = "c1", direction = "to_owner", status = "active", subject = "Website fertig", muted = true,
            endpoint = Endpoint("bt", "Auto", 2), endpoints = listOf(Endpoint("e", "Hörer", 1), Endpoint("bt", "Auto", 2)),
            activeSince = 1792227000000,
        )
        val j = JSONObject(s.toJson())
        assertEquals("active", j.getString("status"))
        assertTrue(j.getBoolean("muted"))
        assertEquals("Auto", j.getJSONObject("endpoint").getString("name"))
        assertEquals(2, j.getJSONArray("endpoints").length())
    }
}
