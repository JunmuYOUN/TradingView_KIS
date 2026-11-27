package com.kistradingview.app

import android.Manifest
import android.app.NotificationManager
import android.os.Build
import android.util.Base64
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.net.HttpURLConnection
import java.net.ServerSocket
import java.net.URL

/**
 * 기기에서만 확인할 수 있는 부분의 검사: Android Keystore 암호화, 앱 안의 Python 서버 시작,
 * 세션 쿠키 접근 제한, 얼러트 알림. PC 에서 도는 tools/check_compat.py 가 볼 수 없는 곳입니다.
 *
 *     gradlew :app:connectedDebugAndroidTest     (기기나 에뮬레이터가 연결되어 있어야 합니다)
 *
 * 디버그 앱(com.kistradingview.app.debug)에서 돌기 때문에 설치된 정식 앱과 그 데이터는 건드리지
 * 않습니다. 정식 앱이 쓰는 8765 포트와 겹치지 않게 빈 포트를 골라 씁니다.
 */
@RunWith(AndroidJUnit4::class)
class OnDeviceTest {
    private class Reply(val code: Int, val body: String, val cookie: String?)

    private fun get(url: String, cookie: String? = null): Reply {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.instanceFollowRedirects = false
        connection.connectTimeout = 10_000
        connection.readTimeout = 30_000
        if (cookie != null) connection.setRequestProperty("Cookie", cookie)
        try {
            val code = connection.responseCode
            val stream = if (code >= 400) connection.errorStream else connection.inputStream
            val body = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            return Reply(code, body, connection.getHeaderField("Set-Cookie")?.substringBefore(';'))
        } finally {
            connection.disconnect()
        }
    }

    /** 일회용 주소를 열어 세션 쿠키를 받습니다 (WebView 가 앱 시작 때 하는 일). */
    private fun openSession(): Pair<String, String> {
        val launchUrl = ChartServer.launch(context)
        val base = launchUrl.substringBefore("/?")
        val first = get(launchUrl)
        assertEquals("일회용 주소는 쿠키를 주고 / 로 보냅니다", 303, first.code)
        return base to checkNotNull(first.cookie) { "세션 쿠키가 없습니다" }
    }

    @Test
    fun keystoreEncryptsAndDecrypts() {
        val plain = Base64.encodeToString("한투 App Secret 예시 값".toByteArray(), Base64.NO_WRAP)
        val sealed = SecretBox.encrypt(plain)
        assertNotEquals(plain, sealed)
        assertNotEquals("IV 가 매번 달라야 합니다", sealed, SecretBox.encrypt(plain))
        assertEquals(plain, SecretBox.decrypt(sealed))
    }

    @Test
    fun keystoreRejectsTamperedData() {
        val sealed = Base64.decode(SecretBox.encrypt(Base64.encodeToString(ByteArray(40) { 7 }, Base64.NO_WRAP)), Base64.NO_WRAP)
        sealed[sealed.size - 1] = (sealed[sealed.size - 1].toInt() xor 1).toByte()
        assertThrows(Exception::class.java) { SecretBox.decrypt(Base64.encodeToString(sealed, Base64.NO_WRAP)) }
    }

    @Test
    fun serverStartsAndServesTheDesktopScreen() {
        val (base, cookie) = openSession()
        val page = get("$base/", cookie)
        assertEquals(200, page.code)
        assertTrue("데스크톱의 index.html 이어야 합니다", page.body.contains("id=\"chart\""))
        assertEquals(200, get("$base/js/app.js", cookie).code)
        assertEquals(200, get("$base/vendor/lightweight-charts.standalone.production.js", cookie).code)

        val config = JSONObject(get("$base/api/config", cookie).body)
        assertTrue(config.getString("source") in listOf("mock", "kis"))
        assertFalse("화면의 차트/설정 탭을 쓰려면 desktop=false 여야 합니다", config.getBoolean("desktop"))
        assertEquals(config.getString("source"), ChartServer.status(context).source)
    }

    @Test
    fun onlyThisAppCanReachTheServer() {
        val (base, cookie) = openSession()
        assertEquals("쿠키 없는 요청(다른 앱)은 거부", 403, get("$base/api/config").code)
        assertEquals(200, get("$base/api/config", cookie).code)
        // 화면이 다시 열리면 세션이 새로 만들어지고, 이전 쿠키는 더 통하지 않습니다.
        val (_, newCookie) = openSession()
        assertEquals(403, get("$base/api/config", cookie).code)
        assertEquals(200, get("$base/api/config", newCookie).code)
    }

    @Test
    fun pythonVaultStoresSecretsThroughKeystore() {
        ChartServer.launch(context)
        val python = Python.getInstance()
        val file = File(context.cacheDir, "vault-probe.bin").apply { delete() }
        try {
            val path = python.getModule("pathlib").callAttr("Path", file.absolutePath)
            val value = python.builtins.callAttr("dict")
            value.callAttr("__setitem__", "KIS_APP_KEY", "검사용-값-12345")
            val vault = python.getModule("server.vault")
            assertTrue("vault.save 가 저장해야 합니다", vault.callAttr("save", path, value).toBoolean())
            assertFalse("파일에 평문이 남으면 안 됩니다", String(file.readBytes(), Charsets.UTF_8).contains("검사용-값-12345"))
            assertEquals("검사용-값-12345", vault.callAttr("load", path).callAttr("get", "KIS_APP_KEY").toString())
        } finally {
            file.delete()
        }
    }

    @Test
    fun alertsBecomeNotificationsOnTheChannelForTheVibrationSetting() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            InstrumentationRegistry.getInstrumentation().uiAutomation
                .grantRuntimePermission(context.packageName, Manifest.permission.POST_NOTIFICATIONS)
        }
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.cancelAll()
        try {
            assertTrue(AlertNotifier.notifyAlert(context, "검사 종목 · 10,000 상향 돌파", vibrate = true))
            assertTrue(AlertNotifier.notifyAlert(context, "검사 종목 · 9,000 하향 돌파", vibrate = false))
            var posted = manager.activeNotifications
            val deadline = System.currentTimeMillis() + 5_000
            while (posted.size < 2 && System.currentTimeMillis() < deadline) {
                Thread.sleep(100)
                posted = manager.activeNotifications
            }
            val byChannel = posted.associate {
                it.notification.channelId to it.notification.extras.getCharSequence("android.text").toString()
            }
            assertEquals("검사 종목 · 10,000 상향 돌파", byChannel[AlertNotifier.CHANNEL_VIBRATE])
            assertEquals("검사 종목 · 9,000 하향 돌파", byChannel[AlertNotifier.CHANNEL_QUIET])
            assertTrue(manager.getNotificationChannel(AlertNotifier.CHANNEL_VIBRATE).shouldVibrate())
            assertFalse(manager.getNotificationChannel(AlertNotifier.CHANNEL_QUIET).shouldVibrate())
        } finally {
            manager.cancelAll()
        }
    }

    @Test
    fun emptyKeysAreRejectedWithoutContactingKis() {
        ChartServer.launch(context)
        if (ChartServer.status(context).hasKey) return  // 저장된 키가 있으면 빈 입력은 "저장된 키로 다시 연결"이라는 뜻입니다
        assertTrue(ChartServer.login(context, "", "").contains("App Key"))
    }

    companion object {
        private val context get() = InstrumentationRegistry.getInstrumentation().targetContext

        @BeforeClass
        @JvmStatic
        fun useFreePort() {
            // 같은 기기에서 정식 앱이 8765 를 쓰고 있어도 검사가 돌 수 있게 합니다.
            if (!Python.isStarted()) {
                Python.start(com.chaquo.python.android.AndroidPlatform(context.applicationContext))
            }
            val port = ServerSocket(0).use { it.localPort }
            Python.getInstance().getModule("android_app.bootstrap").put("PORT", port)
        }
    }
}
