package systems.pje.jarvis_mobile.update

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import androidx.core.content.IntentCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import systems.pje.jarvis_mobile.net.Gateway

/** Result of the install session (explicit, not exported: only our own PendingIntent reaches it). */
class UpdateInstallReceiver : BroadcastReceiver() {
    companion object {
        const val ACTION = "systems.pje.jarvis.UPDATE_INSTALL_STATUS"
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION) return
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val confirm = IntentCompat.getParcelableExtra(intent, Intent.EXTRA_INTENT, Intent::class.java)
        AppUpdater.onInstallResult(context.applicationContext, status, intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE), confirm)
    }
}

/** The new version runs for the first time (also without Sir opening the app): report "installed". */
class UpdatePackageReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                AppUpdater.finishAfterUpdate(context.applicationContext)
                AppUpdater.report(context.applicationContext)
            } finally {
                pending.finish()
            }
        }
    }
}

/** Looks for updates every few hours with network, even if the app is never opened and push is missing. */
class UpdateJobService : JobService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onStartJob(params: JobParameters): Boolean {
        scope.launch {
            try {
                AppUpdater.check(applicationContext, manual = false)
            } finally {
                jobFinished(params, false)
            }
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = true
}

object UpdateJob {
    private const val JOB_ID = 7301
    private const val PERIOD_MS = 6 * 3600_000L

    /** Idempotent; only for a paired phone. */
    fun schedule(context: Context) {
        val js = context.getSystemService(JobScheduler::class.java) ?: return
        if (!Gateway.get(context).paired) { js.cancel(JOB_ID); return }
        if (js.getPendingJob(JOB_ID) != null) return
        val job = JobInfo.Builder(JOB_ID, ComponentName(context, UpdateJobService::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(PERIOD_MS)
            .setPersisted(true)
            .build()
        runCatching { js.schedule(job) }
    }
}
