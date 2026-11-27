package com.kistradingview.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import java.util.concurrent.atomic.AtomicInteger

/**
 * 얼러트를 휴대폰 알림으로 보냅니다.
 *
 * 안드로이드에서 진동 여부는 알림마다가 아니라 알림 채널마다 정해지므로, 진동하는 채널과
 * 진동하지 않는 채널을 따로 두고 설정(설정 → 얼러트 알림 → 진동)에 따라 골라 씁니다.
 * 소리와 방해 금지 모드는 휴대폰의 알림 설정을 그대로 따릅니다.
 */
object AlertNotifier {
    const val CHANNEL_VIBRATE = "price_alert_vibrate"
    const val CHANNEL_QUIET = "price_alert_no_vibrate"
    const val CHANNEL_WATCH = "alert_watch"
    const val WATCH_NOTIFICATION_ID = 1

    private val VIBRATION = longArrayOf(0, 350, 150, 350)
    private val nextId = AtomicInteger(1000)

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannels(
            listOf(
                NotificationChannel(
                    CHANNEL_VIBRATE,
                    context.getString(R.string.channel_alert_vibrate),
                    NotificationManager.IMPORTANCE_HIGH,
                ).apply {
                    enableVibration(true)
                    vibrationPattern = VIBRATION
                },
                NotificationChannel(
                    CHANNEL_QUIET,
                    context.getString(R.string.channel_alert_quiet),
                    NotificationManager.IMPORTANCE_HIGH,
                ).apply { enableVibration(false) },
                // 백그라운드에서 얼러트를 감시하는 동안 떠 있는 알림. 소리도 진동도 없습니다.
                NotificationChannel(
                    CHANNEL_WATCH,
                    context.getString(R.string.channel_watch),
                    NotificationManager.IMPORTANCE_LOW,
                ).apply { setShowBadge(false) },
            )
        )
    }

    fun canNotify(context: Context): Boolean = NotificationManagerCompat.from(context).areNotificationsEnabled()

    private fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context,
        0,
        Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    /** 얼러트 하나를 알립니다. 알림 권한이 없으면 false 를 돌려주고 아무것도 하지 않습니다. */
    fun notifyAlert(context: Context, text: String, vibrate: Boolean): Boolean {
        if (!canNotify(context)) return false
        ensureChannels(context)
        val notification = NotificationCompat.Builder(context, if (vibrate) CHANNEL_VIBRATE else CHANNEL_QUIET)
            .setSmallIcon(R.drawable.ic_stat_candles)
            .setContentTitle(context.getString(R.string.alert_title))
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setCategory(NotificationCompat.CATEGORY_EVENT)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setContentIntent(openApp(context))
            .setAutoCancel(true)
            .build()
        // 권한은 위에서 확인했습니다. 그 사이에 꺼졌다면 시스템이 조용히 무시합니다.
        context.getSystemService(NotificationManager::class.java).notify(nextId.incrementAndGet(), notification)
        return true
    }

    /** 백그라운드 감시 중임을 알리는 고정 알림. */
    fun watchNotification(context: Context, count: Int): Notification {
        ensureChannels(context)
        return NotificationCompat.Builder(context, CHANNEL_WATCH)
            .setSmallIcon(R.drawable.ic_stat_candles)
            .setContentTitle(context.getString(R.string.watch_title, count))
            .setContentText(context.getString(R.string.watch_text))
            .setContentIntent(openApp(context))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    fun updateWatchNotification(context: Context, count: Int) {
        if (canNotify(context)) {
            context.getSystemService(NotificationManager::class.java)
                .notify(WATCH_NOTIFICATION_ID, watchNotification(context, count))
        }
    }
}
