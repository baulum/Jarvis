package systems.pje.jarvis_mobile.notify

import android.app.NotificationChannel
import android.app.NotificationChannelGroup
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.RemoteInput
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.json.JSONObject
import systems.pje.jarvis_mobile.MainActivity
import systems.pje.jarvis_mobile.R
import systems.pje.jarvis_mobile.call.CallManager
import systems.pje.jarvis_mobile.net.Gateway

object Channels {
    const val CALL_INCOMING = "jarvis_call_incoming"
    const val CALL_ONGOING = "jarvis_call_ongoing"
    const val SILENT = "jarvis_silent"
    fun forCategory(c: String) = "jarvis_alert_$c"

    private val CATEGORIES = listOf(
        Triple("task", "Aufgaben", NotificationManager.IMPORTANCE_DEFAULT),
        Triple("appointment", "Termine", NotificationManager.IMPORTANCE_HIGH),
        Triple("customer", "Kunden", NotificationManager.IMPORTANCE_DEFAULT),
        Triple("error", "Fehler", NotificationManager.IMPORTANCE_HIGH),
        Triple("approval", "Freigaben", NotificationManager.IMPORTANCE_HIGH),
        Triple("reminder", "Erinnerungen", NotificationManager.IMPORTANCE_HIGH),
        Triple("system", "Systemstatus", NotificationManager.IMPORTANCE_LOW),
    )

    fun create(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannelGroups(
            listOf(NotificationChannelGroup("calls", "Anrufe"), NotificationChannelGroup("alerts", "Benachrichtigungen")),
        )
        val ring = NotificationChannel(CALL_INCOMING, "Eingehende Anrufe", NotificationManager.IMPORTANCE_HIGH).apply {
            group = "calls"
            description = "Jarvis ruft an"
            setSound(
                RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE),
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build(),
            )
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 900, 700, 900, 700)
            lockscreenVisibility = NotificationCompat.VISIBILITY_PUBLIC
        }
        val ongoing = NotificationChannel(CALL_ONGOING, "Laufende Anrufe", NotificationManager.IMPORTANCE_DEFAULT).apply {
            group = "calls"; setSound(null, null)
        }
        val silent = NotificationChannel(SILENT, "Still (Ruhezeit)", NotificationManager.IMPORTANCE_LOW).apply {
            group = "alerts"; setSound(null, null); enableVibration(false)
        }
        val alerts = CATEGORIES.map { (id, name, importance) ->
            NotificationChannel(forCategory(id), name, importance).apply {
                group = "alerts"; enableLights(true); lightColor = Color.parseColor("#5CE1FF")
            }
        }
        nm.createNotificationChannels(listOf(ring, ongoing, silent) + alerts)
    }
}

/** Renders alert pushes natively – works while the app is not running. */
object AlertNotifier {
    const val KEY_REPLY = "reply_text"

    @android.annotation.SuppressLint("MissingPermission")
    fun show(context: Context, msg: JSONObject) {
        val alertId = msg.optString("alert_id")
        val category = msg.optString("category", "system")
        val silent = msg.optBoolean("silent")
        val nid = alertId.hashCode()
        val link = msg.optString("link").ifBlank { "jarvis://activity" }
        val open = PendingIntent.getActivity(
            context, nid,
            Intent(context, MainActivity::class.java).setAction(Intent.ACTION_VIEW).setData(Uri.parse(link))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val b = NotificationCompat.Builder(context, if (silent) Channels.SILENT else Channels.forCategory(category))
            .setSmallIcon(R.drawable.ic_stat_jarvis)
            .setColor(Color.parseColor(if (msg.optString("level") == "info") "#5CE1FF" else "#FFB547"))
            .setContentTitle(msg.optString("title"))
            .setContentText(msg.optString("body"))
            .setStyle(NotificationCompat.BigTextStyle().bigText(msg.optString("body")))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setGroup("jarvis_$category")
            .setSilent(silent)
            .setCategory(if (category == "reminder" || category == "appointment") NotificationCompat.CATEGORY_REMINDER else NotificationCompat.CATEGORY_MESSAGE)
            .setVisibility(if (category == "approval" || category == "customer") NotificationCompat.VISIBILITY_PRIVATE else NotificationCompat.VISIBILITY_PUBLIC)
        val actions = msg.optJSONArray("actions")
        for (i in 0 until (actions?.length() ?: 0)) {
            when (actions!!.getString(i)) {
                "approve" -> b.addAction(0, "Genehmigen", activity(context, "jarvis://approval/${msg.optString("approval_id")}?decide=approve", nid + 1))
                "reject" -> b.addAction(0, "Ablehnen", broadcast(context, "reject", msg, nid + 2))
                "snooze" -> b.addAction(0, "Später erinnern", broadcast(context, "snooze", msg, nid + 3))
                "call" -> b.addAction(0, "Jarvis anrufen", broadcast(context, "call", msg, nid + 4))
                "reply" -> {
                    val input = RemoteInput.Builder(KEY_REPLY).setLabel("Antwort an Jarvis").build()
                    val pi = PendingIntent.getBroadcast(
                        context, nid + 5,
                        Intent(context, AlertActionReceiver::class.java).setAction("reply").putExtra("alert", msg.toString()).putExtra("nid", nid),
                        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE, // RemoteInput needs a mutable intent
                    )
                    b.addAction(NotificationCompat.Action.Builder(0, "Antworten", pi).addRemoteInput(input).setAllowGeneratedReplies(false).build())
                }
            }
        }
        val nm = NotificationManagerCompat.from(context)
        runCatching {
            nm.notify(nid, b.build())
            nm.notify(
                "summary_$category".hashCode(),
                NotificationCompat.Builder(context, if (silent) Channels.SILENT else Channels.forCategory(category))
                    .setSmallIcon(R.drawable.ic_stat_jarvis).setGroup("jarvis_$category").setGroupSummary(true)
                    .setSilent(true).setAutoCancel(true).build(),
            )
        }.onFailure { Log.w("JarvisAlert", "notify failed: ${it.message}") }
    }

    private fun activity(context: Context, link: String, rc: Int) = PendingIntent.getActivity(
        context, rc,
        Intent(context, MainActivity::class.java).setAction(Intent.ACTION_VIEW).setData(Uri.parse(link))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun broadcast(context: Context, action: String, msg: JSONObject, rc: Int) = PendingIntent.getBroadcast(
        context, rc,
        Intent(context, AlertActionReceiver::class.java).setAction(action).putExtra("alert", msg.toString()).putExtra("nid", msg.optString("alert_id").hashCode()),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )
}

/** Notification actions that do not need the UI: reject (signed), snooze, reply, call. */
class AlertActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val msg = JSONObject(intent.getStringExtra("alert") ?: return)
        val nid = intent.getIntExtra("nid", 0)
        val nm = NotificationManagerCompat.from(context)
        if (intent.action == "call") {
            nm.cancel(nid); CallManager.startOutgoing(context); return
        }
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            val gw = Gateway.get(context)
            val alertId = msg.optString("alert_id")
            val result = runCatching {
                when (intent.action) {
                    "reject" -> {
                        val aid = msg.optString("approval_id")
                        gw.request("POST", "/api/mobile/approvals/$aid", JSONObject().put("approve", false).toString(), signPurpose = "approval:$aid")
                        "Abgelehnt"
                    }
                    "snooze" -> {
                        gw.request("POST", "/api/mobile/alerts/$alertId/snooze", JSONObject().put("minutes", 30).toString())
                        "Erinnerung in 30 Minuten"
                    }
                    "reply" -> {
                        val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(AlertNotifier.KEY_REPLY)?.toString().orEmpty()
                        if (text.isNotBlank()) gw.request("POST", "/api/mobile/alerts/$alertId/reply", JSONObject().put("text", text).toString())
                        "Gesendet"
                    }
                    else -> null
                }
            }
            confirm(context, nid, msg, result.getOrNull() ?: ("Fehlgeschlagen: " + (result.exceptionOrNull()?.message ?: "?")))
            pending.finish()
        }
    }

    @android.annotation.SuppressLint("MissingPermission")
    private fun confirm(context: Context, nid: Int, msg: JSONObject, text: String) {
        val category = msg.optString("category", "system")
        runCatching {
            NotificationManagerCompat.from(context).notify(
                nid,
                NotificationCompat.Builder(context, Channels.SILENT)
                    .setSmallIcon(R.drawable.ic_stat_jarvis).setColor(Color.parseColor("#5CE1FF"))
                    .setContentTitle(msg.optString("title")).setContentText(text)
                    .setGroup("jarvis_$category").setTimeoutAfter(6000).setAutoCancel(true).build(),
            )
        }
    }
}
