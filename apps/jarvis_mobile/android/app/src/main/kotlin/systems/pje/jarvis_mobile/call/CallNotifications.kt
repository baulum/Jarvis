package systems.pje.jarvis_mobile.call

import android.app.Notification
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.Color
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.graphics.drawable.IconCompat
import systems.pje.jarvis_mobile.MainActivity
import systems.pje.jarvis_mobile.R
import systems.pje.jarvis_mobile.notify.Channels

/** CallStyle notifications: incoming (full-screen on the lock screen, answer/decline), ongoing (hang up). */
class CallNotifications(private val context: Context) {
    companion object {
        const val ID = 4711
    }

    private val jarvis = Person.Builder()
        .setName("J.A.R.V.I.S.")
        .setIcon(IconCompat.createWithResource(context, R.drawable.ic_jarvis_person))
        .setImportant(true)
        .build()

    fun build(s: CallState): Notification {
        val open = PendingIntent.getActivity(
            context, 1,
            Intent(context, MainActivity::class.java).setAction(MainActivity.ACTION_SHOW_CALL)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val ringing = s.status == "ringing"
        val style = if (ringing) {
            // Answer opens the call screen (activity) – a notification trampoline through a receiver is not allowed.
            val answer = PendingIntent.getActivity(
                context, 2,
                Intent(context, MainActivity::class.java).setAction(MainActivity.ACTION_ANSWER)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            NotificationCompat.CallStyle.forIncomingCall(jarvis, action("decline"), answer)
        } else {
            NotificationCompat.CallStyle.forOngoingCall(jarvis, action("hangup"))
        }
        val text = when (s.status) {
            "ringing" -> s.subject.ifBlank { "Eingehender Anruf" }
            "dialing", "connecting" -> "Verbinde …"
            "reconnecting" -> "Verbindung wird wiederhergestellt …"
            "on_hold" -> "Gehalten"
            else -> "Verbunden"
        }
        return NotificationCompat.Builder(context, if (ringing) Channels.CALL_INCOMING else Channels.CALL_ONGOING)
            .setSmallIcon(R.drawable.ic_stat_jarvis)
            .setColor(Color.parseColor("#5CE1FF"))
            .setContentTitle("J.A.R.V.I.S.")
            .setContentText(text)
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setContentIntent(open)
            .apply { if (ringing) setFullScreenIntent(open, true) }
            .setOngoing(true)
            .setOnlyAlertOnce(!ringing)
            .apply { s.activeSince?.let { setWhen(it).setUsesChronometer(true) } }
            .build()
    }

    private fun action(name: String): PendingIntent = PendingIntent.getBroadcast(
        context, name.hashCode(),
        Intent(context, CallActionReceiver::class.java).setAction(name),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    @android.annotation.SuppressLint("MissingPermission")
    fun update(s: CallState) {
        if (s.status == "idle" || s.status == "ended") return
        runCatching { NotificationManagerCompat.from(context).notify(ID, build(s)) }
    }

    fun cancel() = NotificationManagerCompat.from(context).cancel(ID)
}

class CallActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            "decline" -> CallManager.decline(context)
            "hangup" -> CallManager.hangup(context)
        }
    }
}
