package com.kistradingview.app

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat

/**
 * 앱이 화면에서 벗어난 동안 얼러트 감시가 끊기지 않게 프로세스와 CPU 를 깨워 두는 서비스.
 *
 * 시세를 받아 얼러트를 판정하는 일은 WebView 안의 데스크톱 화면 코드가 그대로 합니다. 이 서비스는
 * 그 코드가 계속 돌 수 있게만 해 줍니다. 열어 둔 종목에 얼러트가 있을 때만 MainActivity 가 켜고,
 * 앱으로 돌아오거나 얼러트가 다 사라지면 끕니다.
 */
class AlertWatchService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val count = intent?.getIntExtra(EXTRA_COUNT, 1) ?: 1
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else {
            0
        }
        ServiceCompat.startForeground(
            this, AlertNotifier.WATCH_NOTIFICATION_ID, AlertNotifier.watchNotification(this, count), type,
        )
        if (wakeLock == null) {
            // 화면이 꺼져도 시세 갱신이 멈추지 않게 합니다. 서비스가 끝나면 바로 풉니다.
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "KisTradingView:alerts")
                .apply { acquire(MAX_WATCH_MILLIS) }
        }
        return START_NOT_STICKY  // 프로세스가 죽으면 화면(WebView)도 없으므로 혼자 되살아나지 않습니다
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        stopSelf()  // 최근 앱 목록에서 앱을 밀어 닫으면 감시도 끝납니다
    }

    override fun onDestroy() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val EXTRA_COUNT = "count"
        private const val MAX_WATCH_MILLIS = 12 * 60 * 60 * 1000L

        /** 앱이 아직 화면에 보이는 동안(onPause)에만 부르세요. 백그라운드에서는 시스템이 시작을 막습니다. */
        fun start(context: Context, count: Int) {
            ContextCompat.startForegroundService(
                context, Intent(context, AlertWatchService::class.java).putExtra(EXTRA_COUNT, count),
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, AlertWatchService::class.java))
        }
    }
}
