package systems.pje.jarvis_mobile.assist

import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Paint
import android.net.Uri
import android.os.SystemClock
import android.util.AttributeSet
import android.view.LayoutInflater
import android.view.View
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import systems.pje.jarvis_mobile.MainActivity
import systems.pje.jarvis_mobile.R
import kotlin.math.min
import kotlin.math.sin

private const val CYAN = 0xFF5CE1FF.toInt()
private const val ICE = 0xFFCFF4FF.toInt()
private const val AMBER = 0xFFFFB547.toInt()
private const val DIM = 0xFF4A6573.toInt()

/**
 * The bottom sheet shown over the current screen – used by both the assistant session (default assistant) and
 * [AssistOverlayActivity] (wake word / notification). Tap outside or ✕ closes it.
 */
class AssistPanel(context: Context, private val host: Host) {
    interface Host {
        fun close()
        fun startActivity(intent: Intent)
    }

    val root: View = LayoutInflater.from(context).inflate(R.layout.assist_panel, null, false)
    private val card: View = root.findViewById(R.id.assist_card)
    private val pulse: PulseView = root.findViewById(R.id.assist_pulse)
    private val status: TextView = root.findViewById(R.id.assist_status)
    private val heard: TextView = root.findViewById(R.id.assist_heard)
    private val scroll: View = root.findViewById(R.id.assist_scroll)
    private val answer: TextView = root.findViewById(R.id.assist_answer)
    private val talk: TextView = root.findViewById(R.id.assist_talk)
    private val stop: TextView = root.findViewById(R.id.assist_stop)
    private val openApp: TextView = root.findViewById(R.id.assist_open)
    private val session = AssistSession(context, ::render)
    private val autoClose = Runnable { host.close() }

    init {
        root.setOnClickListener { host.close() }
        card.setOnClickListener { } // taps on the card must not close it
        root.findViewById<View>(R.id.assist_close).setOnClickListener { host.close() }
        pulse.setOnClickListener { if (session.ui.phase == Phase.LISTENING) session.finishSpeaking() else listen() }
        talk.setOnClickListener { listen() }
        stop.setOnClickListener { session.interrupt() }
        openApp.setOnClickListener {
            val link = session.sessionId?.let { "jarvis://chat/$it" } ?: "jarvis://chat"
            host.startActivity(
                Intent(root.context, MainActivity::class.java).setAction(Intent.ACTION_VIEW).setData(Uri.parse(link))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            )
            host.close()
        }
        val pad = card.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(card) { v, insets ->
            v.updatePadding(bottom = pad + insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime()).bottom)
            insets
        }
    }

    fun start() {
        card.translationY = 400f * root.resources.displayMetrics.density
        card.alpha = 0f
        card.animate().translationY(0f).alpha(1f).setDuration(260).start()
        listen()
    }

    /** Shown instead of listening (e.g. phone locked); stays until Sir acts. */
    fun message(text: String) {
        session.message(text)
        talk.visibility = View.GONE
        root.removeCallbacks(autoClose)
    }

    fun stop() {
        root.removeCallbacks(autoClose)
        session.stop()
    }

    fun release() {
        root.removeCallbacks(autoClose)
        session.release()
    }

    private fun listen() {
        root.removeCallbacks(autoClose)
        session.listen()
    }

    private fun render(s: AssistUi) {
        status.text = s.status
        status.visibility = if (s.status.isEmpty()) View.GONE else View.VISIBLE
        status.setTextColor(if (s.phase == Phase.ERROR) AMBER else ICE)
        heard.text = if (s.heard.isEmpty()) "" else "„${s.heard}“"
        heard.visibility = if (s.heard.isEmpty()) View.GONE else View.VISIBLE
        answer.text = s.answer
        scroll.visibility = if (s.answer.isEmpty()) View.GONE else View.VISIBLE
        val idle = s.phase == Phase.IDLE || s.phase == Phase.ERROR
        talk.visibility = if (idle) View.VISIBLE else View.GONE
        stop.visibility = if (s.phase == Phase.TRANSCRIBING || s.phase == Phase.THINKING || s.phase == Phase.SPEAKING) View.VISIBLE else View.GONE
        openApp.visibility = if (idle && s.answer.isNotEmpty()) View.VISIBLE else View.GONE
        pulse.color = when (s.phase) {
            Phase.ERROR -> AMBER
            Phase.IDLE -> DIM
            else -> CYAN
        }
        pulse.busy = s.phase == Phase.TRANSCRIBING || s.phase == Phase.THINKING || s.phase == Phase.SPEAKING
        pulse.level = s.level
        pulse.contentDescription = when (s.phase) {
            Phase.LISTENING -> "Fertig gesprochen"
            Phase.TRANSCRIBING, Phase.THINKING, Phase.SPEAKING -> "Unterbrechen und sprechen"
            else -> "Erneut sprechen"
        }
        root.removeCallbacks(autoClose)
        if (idle) root.postDelayed(autoClose, if (s.answer.isEmpty()) 8_000 else 45_000)
    }
}

/** The small Jarvis core: breathes while working, follows the microphone level while listening. */
class PulseView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : View(context, attrs) {
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private var shown = 0f

    var color = CYAN
        set(v) {
            field = v; invalidate()
        }
    var busy = false
        set(v) {
            field = v; invalidate()
        }
    var level = 0f
        set(v) {
            field = v; invalidate()
        }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val max = min(cx, cy)
        val core = max * 0.38f
        shown += (level - shown) * 0.35f
        val t = (SystemClock.uptimeMillis() % 1400) / 1400.0
        val spread = if (busy) (0.5 + 0.5 * sin(t * 2 * Math.PI)).toFloat() else shown
        ring.strokeWidth = max * 0.08f
        ring.color = color
        ring.alpha = (90 + 120 * spread).toInt()
        canvas.drawCircle(cx, cy, core + (max - core - ring.strokeWidth) * spread, ring)
        fill.color = color
        canvas.drawCircle(cx, cy, core, fill)
        if (busy || kotlin.math.abs(level - shown) > 0.01f) postInvalidateOnAnimation()
    }
}

/** ScrollView that grows with its text up to a maximum height. */
class MaxHeightScrollView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : ScrollView(context, attrs) {
    private val maxHeightPx = (300 * resources.displayMetrics.density).toInt()

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(maxHeightPx, MeasureSpec.AT_MOST))
    }
}
