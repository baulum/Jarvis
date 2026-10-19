package systems.pje.jarvis_mobile.car

import android.content.Intent
import android.content.pm.ApplicationInfo
import androidx.car.app.CarAppService
import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.model.Action
import androidx.car.app.model.CarColor
import androidx.car.app.model.CarIcon
import androidx.car.app.model.ItemList
import androidx.car.app.model.ListTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.car.app.validation.HostValidator
import androidx.core.graphics.drawable.IconCompat
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import systems.pje.jarvis_mobile.R
import systems.pje.jarvis_mobile.call.CallManager

/**
 * Android Auto entry (category CALLING, beta on Google's side). Incoming/ongoing calls are shown by Android
 * Auto itself through the Telecom integration ("in-call view replaces the app's screens"); this templated
 * screen lets Sir start a call from the car. Sideloaded apps only appear with Android Auto's developer
 * setting "Unknown sources"; Play distribution of calling apps is restricted to internal/closed testing.
 */
class JarvisCarAppService : CarAppService() {
    override fun createHostValidator(): HostValidator =
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
        else HostValidator.Builder(applicationContext).addAllowedHosts(androidx.car.app.R.array.hosts_allowlist_sample).build()

    override fun onCreateSession(): Session = object : Session() {
        override fun onCreateScreen(intent: Intent): Screen = JarvisCarScreen(carContext)
    }
}

class JarvisCarScreen(ctx: CarContext) : Screen(ctx) {
    init {
        lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onCreate(owner: LifecycleOwner) {
                lifecycleScope.launch { CallManager.state.collect { invalidate() } }
            }
        })
    }

    override fun onGetTemplate(): Template {
        val s = CallManager.state.value
        val icon = CarIcon.Builder(IconCompat.createWithResource(carContext, R.drawable.ic_jarvis_person)).setTint(CarColor.BLUE).build()
        val list = ItemList.Builder()
        if (!CallManager.busy) {
            list.addItem(
                Row.Builder().setTitle("Jarvis anrufen").addText("Sprechen Sie mit Ihrem Assistenten")
                    .setImage(icon).setOnClickListener { CallManager.startOutgoing(carContext) }.build(),
            )
        } else {
            list.addItem(Row.Builder().setTitle("Anruf: ${statusText(s.status)}").setImage(icon).build())
            list.addItem(Row.Builder().setTitle("Auflegen").setOnClickListener { CallManager.hangup(carContext) }.build())
        }
        s.error?.let { list.addItem(Row.Builder().setTitle("Hinweis").addText(it.take(80)).build()) }
        return ListTemplate.Builder().setTitle("Jarvis").setHeaderAction(Action.APP_ICON).setSingleList(list.build()).build()
    }

    private fun statusText(s: String) = when (s) {
        "ringing" -> "klingelt"; "dialing", "connecting" -> "verbinde"; "active" -> "verbunden"
        "reconnecting" -> "Verbindung wird wiederhergestellt"; "on_hold" -> "gehalten"; else -> s
    }
}
