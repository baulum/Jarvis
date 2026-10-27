package systems.pje.jarvis_mobile

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import systems.pje.jarvis_mobile.update.ReleaseInfo
import systems.pje.jarvis_mobile.update.UpdatePolicy

class UpdatePolicyTest {
    private val signer = "ab".repeat(32)
    private val manifest = JSONObject(
        """{"application_id":"systems.pje.jarvis","version_code":3002,"version_name":"1.0.0+3002.abc1234",
           "sha256":"${"CD".repeat(32)}","size":5000000,"signing_cert_sha256":"${signer.chunked(2).joinToString(":").uppercase()}",
           "download_path":"/api/mobile/app-update/releases/3002/apk","withdrawn":false,
           "changelog":[{"commit":"abc1234","subject":"Updater"},{"commit":"def5678","subject":""}]}""",
    )
    private val rel = ReleaseInfo.fromJson(manifest)

    @Test fun manifestIsParsedAndNormalized() {
        assertEquals(3002L, rel.versionCode)
        assertEquals(signer, rel.signer)
        assertEquals("cd".repeat(32), rel.sha256)
        assertEquals(listOf("Updater"), rel.changelog)
    }

    @Test fun newerReleaseWithSameKeyIsOffered() {
        assertNull(UpdatePolicy.rejectReason(rel, "systems.pje.jarvis", 3001, signer))
    }

    @Test fun sameOrOlderVersionIsNotOffered() {
        assertNotNull(UpdatePolicy.rejectReason(rel, "systems.pje.jarvis", 3002, signer))
        assertNotNull(UpdatePolicy.rejectReason(rel, "systems.pje.jarvis", 4000, signer))
    }

    @Test fun otherKeyOrPackageIsRejected() {
        assertTrue(UpdatePolicy.rejectReason(rel, "systems.pje.jarvis", 1, "00".repeat(32))!!.contains("Signaturschlüssel"))
        assertNotNull(UpdatePolicy.rejectReason(rel, "systems.pje.jarvis", 1, null))
        assertNotNull(UpdatePolicy.rejectReason(rel, "systems.pje.jarvis.debug", 1, signer))
    }

    @Test fun withdrawnOrIncompleteReleaseIsRejected() {
        assertNotNull(UpdatePolicy.rejectReason(rel.copy(withdrawn = true), "systems.pje.jarvis", 1, signer))
        assertNotNull(UpdatePolicy.rejectReason(rel.copy(sha256 = "xyz"), "systems.pje.jarvis", 1, signer))
        assertNotNull(UpdatePolicy.rejectReason(rel.copy(size = 0), "systems.pje.jarvis", 1, signer))
        assertNotNull(UpdatePolicy.rejectReason(rel.copy(downloadPath = "https://evil.example/x.apk"), "systems.pje.jarvis", 1, signer))
    }

    @Test fun brokenDownloadResumesOnlyWhenPlausible() {
        assertEquals(1000L, UpdatePolicy.resumeFrom(1000, 5000))
        assertEquals(0L, UpdatePolicy.resumeFrom(0, 5000))
        assertEquals(0L, UpdatePolicy.resumeFrom(5000, 5000))
        assertEquals(0L, UpdatePolicy.resumeFrom(6000, 5000))
    }

    @Test fun contentRangeIsParsed() {
        assertEquals(1000L, UpdatePolicy.contentRangeStart("bytes 1000-4999/5000"))
        assertEquals(0L, UpdatePolicy.contentRangeStart("bytes 0-4999/*"))
        assertNull(UpdatePolicy.contentRangeStart(null))
        assertNull(UpdatePolicy.contentRangeStart("items 1-2/3"))
    }

    @Test fun backoffGrowsAndIsCapped() {
        assertEquals(0L, UpdatePolicy.backoffMs(0))
        assertEquals(5 * 60_000L, UpdatePolicy.backoffMs(1))
        assertEquals(10 * 60_000L, UpdatePolicy.backoffMs(2))
        assertEquals(6 * 3600_000L, UpdatePolicy.backoffMs(30))
    }

    @Test fun automaticChecksAreThrottled() {
        val now = 10_000_000L
        assertTrue(UpdatePolicy.checkDue(0, now, false))
        assertFalse(UpdatePolicy.checkDue(now - 60_000, now, false))
        assertTrue(UpdatePolicy.checkDue(now - 60_000, now, true))
        assertTrue(UpdatePolicy.checkDue(now - UpdatePolicy.CHECK_INTERVAL_MS, now, false))
        assertTrue(UpdatePolicy.checkDue(now + 60_000, now, false)) // clock went back
    }

    @Test fun installWaitsForBackgroundAndNeverDuringCalls() {
        assertTrue(UpdatePolicy.mayInstall(userInitiated = false, foreground = false, callActive = false))
        assertFalse(UpdatePolicy.mayInstall(userInitiated = false, foreground = true, callActive = false))
        assertTrue(UpdatePolicy.mayInstall(userInitiated = true, foreground = true, callActive = false))
        assertFalse(UpdatePolicy.mayInstall(userInitiated = true, foreground = false, callActive = true))
    }

    @Test fun unattendedFromAndroid12() {
        assertFalse(UpdatePolicy.unattendedPossible(30))
        assertTrue(UpdatePolicy.unattendedPossible(31))
    }

    @Test fun brokenFilesAreDiscardedOnlyForPermanentFailures() {
        assertTrue(UpdatePolicy.discardAfter(4))
        assertTrue(UpdatePolicy.discardAfter(5))
        assertFalse(UpdatePolicy.discardAfter(3)) // aborted by Sir: the file stays for "Jetzt installieren"
        assertFalse(UpdatePolicy.discardAfter(6))
    }
}
