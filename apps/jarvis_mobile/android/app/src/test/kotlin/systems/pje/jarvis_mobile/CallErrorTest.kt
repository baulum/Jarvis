package systems.pje.jarvis_mobile

import io.livekit.android.events.DisconnectReason
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import systems.pje.jarvis_mobile.call.disconnectError

class CallErrorTest {
    @Test fun normalEndShowsNoError() {
        assertNull(disconnectError(DisconnectReason.CLIENT_INITIATED, null))
        assertNull(disconnectError(DisconnectReason.ROOM_DELETED, null)) // the core closed the room after a hang-up
        assertNull(disconnectError(DisconnectReason.ROOM_CLOSED, null))
    }

    @Test fun droppedConnectionNamesTheReasonInsteadOfACoroutine() {
        // 2026-10-28 the screen only said "A0 was cancelled" (R8 name of StandaloneCoroutine) after the server dropped the phone
        assertEquals("Audioverbindung abgebrochen (STATE_MISMATCH)", disconnectError(DisconnectReason.STATE_MISMATCH, null))
        assertEquals(
            "Audioverbindung abgebrochen (SIGNAL_CLOSE): socket closed",
            disconnectError(DisconnectReason.SIGNAL_CLOSE, RuntimeException("socket closed")),
        )
        assertEquals("Audioverbindung abgebrochen (UNKNOWN_REASON)", disconnectError(DisconnectReason.UNKNOWN_REASON, RuntimeException(" ")))
    }
}
