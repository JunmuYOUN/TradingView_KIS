package com.kistradingview.app

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.DialogInterface
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.Settings
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.webkit.ScriptHandler
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import org.json.JSONObject
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * 차트 화면. 앱 안의 Python 서버를 켜고, 데스크톱 앱과 같은 웹 화면을 WebView 에 띄웁니다.
 *
 * 데스크톱 창의 "계정" 메뉴(한투 API 로그인, 로그아웃)는 여기서 안드로이드 대화상자로 만듭니다.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var webView: ChartWebView
    private lateinit var statusPanel: View
    private lateinit var statusProgress: View
    private lateinit var statusText: TextView
    private lateinit var statusRetry: Button

    private val worker: ExecutorService = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())

    /** 차트 서버의 주소 (예: http://127.0.0.1:8765). 이 주소의 페이지만 WebView 안에서 엽니다. */
    private var origin: String? = null
    private var overlayHandler: ScriptHandler? = null
    private var bridgeListening = false
    private var pageFailed = false
    private var renewedSession = false

    /** 열어 둔 종목에 걸린 얼러트 수 (화면이 알려 줍니다). 0보다 크면 앱이 화면에 없어도 감시를 계속합니다. */
    private var watchCount = 0
    private var watchServiceRunning = false
    private var started = false
    private var resumed = false
    private var askedForNotifications = false
    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) Toast.makeText(this, R.string.notifications_denied, Toast.LENGTH_LONG).show()
        }

    /** 데스크톱 화면 위에 얹는 모바일 보정 (assets/overlay). 데스크톱의 web/ 은 고치지 않습니다. */
    private val overlaySource: String by lazy {
        fun asset(name: String) = assets.open("overlay/$name").bufferedReader().use { it.readText() }
        "window.__kisAndroidCss = ${JSONObject.quote(asset("android.css"))};\n${asset("android.js")}"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.web)
        statusPanel = findViewById(R.id.status_panel)
        statusProgress = findViewById(R.id.status_progress)
        statusText = findViewById(R.id.status_text)
        statusRetry = findViewById(R.id.status_retry)

        // 상태 표시줄·내비게이션 바·노치·키보드에 화면이 가려지지 않게 그만큼 안쪽으로 들입니다.
        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.root)) { view, insets ->
            val types = WindowInsetsCompat.Type.systemBars() or
                WindowInsetsCompat.Type.displayCutout() or
                WindowInsetsCompat.Type.ime()
            val bars = insets.getInsets(types)
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        AlertNotifier.ensureChannels(this)
        setUpWebView()
        statusRetry.setOnClickListener { openChart() }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                // 열린 창이나 설정 탭이 있으면 화면이 먼저 닫고, 없으면 앱을 뒤로 보냅니다.
                webView.evaluateJavascript("!!(window.__kisAndroid && window.__kisAndroid.back())") { handled ->
                    if (handled != "true") moveTaskToBack(true)
                }
            }
        })
        openChart()
    }

    override fun onStart() {
        super.onStart()
        started = true
        webView.onResume()
        webView.resumeTimers()
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        stopWatchService()  // 화면에 보이는 동안에는 감시용 서비스가 필요 없습니다
    }

    override fun onPause() {
        resumed = false
        // 얼러트가 걸려 있으면 화면에서 벗어나도 감시를 이어 갑니다. 서비스는 아직 화면에 보이는
        // 지금 시작해야 합니다. 완전히 가려진 뒤에는 시스템이 시작을 막습니다.
        if (watchCount > 0 && !isFinishing) startWatchService()
        super.onPause()
    }

    override fun onStop() {
        started = false
        // 감시할 얼러트가 없으면, 화면이 보이지 않는 동안 시세 갱신(5초마다 한투 조회)을 멈춥니다.
        if (!watchServiceRunning) pauseChart()
        super.onStop()
    }

    override fun onDestroy() {
        stopWatchService()
        worker.shutdown()
        (webView.parent as? ViewGroup)?.removeView(webView)
        webView.destroy()
        super.onDestroy()
    }

    // ------------------------------------------------------------ 차트 열기

    private fun openChart() {
        statusPanel.isVisible = true
        statusProgress.isVisible = true
        statusRetry.isVisible = false
        statusText.setText(R.string.starting)
        webView.visibility = View.INVISIBLE
        worker.execute {
            val result = runCatching { ChartServer.launch(this) }
            ui.post {
                if (isDestroyed) return@post
                result.onSuccess(::load).onFailure(::showFailure)
            }
        }
    }

    private fun load(launchUrl: String) {
        val newOrigin = originOf(launchUrl.toUri())
        if (newOrigin != origin) {
            origin = newOrigin
            installPageHooks(newOrigin)
        }
        webView.loadUrl(launchUrl)
    }

    private fun reloadChart() {
        origin?.let { webView.loadUrl("$it/") }
    }

    private fun showFailure(error: Throwable?) {
        if (error != null) Log.e(TAG, "차트를 열지 못했습니다", error)
        val reason = (error as? ChartServer.Failure)?.message.orEmpty()
        statusPanel.isVisible = true
        statusProgress.isVisible = false
        statusRetry.isVisible = true
        statusText.text = listOf(getString(R.string.start_failed), reason).filter { it.isNotBlank() }.joinToString("\n")
        webView.visibility = View.INVISIBLE
    }

    private fun originOf(uri: Uri) = "${uri.scheme}://${uri.encodedAuthority}"

    private fun isChartPage(uri: Uri) = origin != null && originOf(uri) == origin

    // ------------------------------------------------------------ WebView

    @SuppressLint("SetJavaScriptEnabled")
    private fun setUpWebView() {
        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true  // 지표 즐겨찾기, 내 스크립트, 마지막으로 본 종목을 localStorage 에 둡니다
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)
            setSupportZoom(false)
            textZoom = 100
        }
        webView.setBackgroundColor(getColor(R.color.chart_background))
        webView.overScrollMode = View.OVER_SCROLL_NEVER
        webView.webViewClient = ChartClient()
        webView.webChromeClient = WebChromeClient()  // alert/confirm, 색 선택 창
        CookieManager.getInstance().setAcceptCookie(true)  // 차트 서버의 세션 쿠키
        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true)
    }

    /** 보정 스크립트와 "계정" 버튼의 통로를 차트 서버 주소에만 연결합니다. */
    @SuppressLint("JavascriptInterface", "RequiresFeature")
    private fun installPageHooks(chartOrigin: String) {
        val rules = setOf(chartOrigin)
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            overlayHandler?.remove()
            overlayHandler = WebViewCompat.addDocumentStartJavaScript(webView, overlaySource, rules)
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            if (bridgeListening) WebViewCompat.removeWebMessageListener(webView, BRIDGE)
            WebViewCompat.addWebMessageListener(webView, BRIDGE, rules) { _, message, _, isMainFrame, _ ->
                if (isMainFrame) onPageMessage(message.data.orEmpty())
            }
            bridgeListening = true
        } else {
            // 아주 오래된 WebView. 이 WebView 는 차트 서버 주소만 열기 때문에 다른 사이트에는 노출되지 않습니다.
            webView.addJavascriptInterface(LegacyBridge(), BRIDGE)
        }
    }

    private inner class LegacyBridge {
        @JavascriptInterface
        fun postMessage(message: String) {
            ui.post { onPageMessage(message) }
        }
    }

    /** 화면(assets/overlay/android.js)이 보낸 메시지. */
    private fun onPageMessage(message: String) {
        if (isFinishing || isDestroyed) return
        val json = runCatching { JSONObject(message) }.getOrNull() ?: return
        when (json.optString("type")) {
            "account" -> if (resumed) showAccount()
            "alert" -> {
                val text = json.optString("text").trim().take(300)
                if (text.isNotEmpty()) AlertNotifier.notifyAlert(this, text, json.optBoolean("vibrate", true))
            }
            "watch" -> setWatchCount(json.optInt("count").coerceIn(0, 999))
            "vibrate" -> if (json.optBoolean("on")) buzz()
            "notification-settings" -> openNotificationSettings()
        }
    }

    // ------------------------------------------------------------ 얼러트 알림과 백그라운드 감시

    private fun setWatchCount(count: Int) {
        if (count == watchCount) return
        watchCount = count
        val watching = count > 0
        // 감시 중에는 화면이 가려져도 페이지의 타이머가 느려지지 않게 하고, 렌더러의 우선순위도 유지합니다.
        webView.stayVisible = watching
        webView.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, !watching)
        when {
            watching && resumed -> askForNotificationPermission()
            watching && watchServiceRunning -> AlertNotifier.updateWatchNotification(this, count)
            !watching && !resumed -> {
                // 백그라운드에서 마지막 얼러트가 울리고 사라졌습니다. 더 감시할 것이 없으니 쉽니다.
                stopWatchService()
                if (!started) pauseChart()
            }
        }
    }

    private fun startWatchService() {
        try {
            AlertWatchService.start(this, watchCount)
            watchServiceRunning = true
        } catch (e: RuntimeException) {
            // 시스템이 시작을 허용하지 않는 상태입니다. 이 경우 화면에 있을 때만 얼러트가 울립니다.
            Log.w(TAG, "얼러트 감시 서비스를 시작하지 못했습니다 (${e.javaClass.simpleName})")
        }
    }

    private fun stopWatchService() {
        if (watchServiceRunning) AlertWatchService.stop(this)
        watchServiceRunning = false
    }

    private fun pauseChart() {
        webView.onPause()
        webView.pauseTimers()
    }

    private fun askForNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU || askedForNotifications) return
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        askedForNotifications = true
        notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    /** 설정에서 진동을 켰을 때 한 번 울려서 확인시켜 줍니다. */
    private fun buzz() {
        val vibrator = getSystemService(Vibrator::class.java) ?: return
        if (vibrator.hasVibrator()) {
            vibrator.vibrate(VibrationEffect.createOneShot(150, VibrationEffect.DEFAULT_AMPLITUDE))
        }
    }

    private fun openNotificationSettings() {
        try {
            startActivity(
                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
            )
        } catch (_: ActivityNotFoundException) {
            // 이 화면이 없는 기기에서는 아무 일도 하지 않습니다.
        }
    }

    private inner class ChartClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (isChartPage(request.url)) return false
            openInBrowser(request.url)  // 오픈소스 정보의 링크 등
            return true
        }

        override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
            pageFailed = false
            setWatchCount(0)  // 새로 열린 화면이 얼러트 수를 다시 알려 줍니다
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (pageFailed || !isChartPage(url.toUri())) return
            if (overlayHandler == null) view.evaluateJavascript(overlaySource, null)
            renewedSession = false
            statusPanel.isVisible = false
            webView.visibility = View.VISIBLE
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            pageFailed = true
            Log.w(TAG, "화면을 불러오지 못했습니다: ${error.errorCode} ${error.description}")
            showFailure(null)
        }

        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            if (!request.isForMainFrame) return
            pageFailed = true
            if (response.statusCode == 403 && !renewedSession) {
                // 세션 쿠키가 더는 맞지 않습니다. 일회용 주소를 새로 받아 한 번 더 엽니다.
                renewedSession = true
                openChart()
            } else {
                showFailure(null)
            }
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            Log.w(TAG, "WebView 렌더러가 종료되어 화면을 다시 만듭니다")
            recreate()
            return true
        }
    }

    private fun openInBrowser(uri: Uri) {
        if (uri.scheme != "https" && uri.scheme != "http") return
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (_: ActivityNotFoundException) {
            // 브라우저가 없는 기기에서는 아무 일도 하지 않습니다.
        }
    }

    // ------------------------------------------------------------ 계정

    private fun showAccount() {
        worker.execute {
            val status = runCatching { ChartServer.status(this) }.getOrNull()
            ui.post {
                if (isFinishing || isDestroyed) return@post
                val connected = status?.source == "kis"
                val title = when {
                    !connected -> R.string.status_mock
                    status?.env == "real" -> R.string.status_kis_real
                    else -> R.string.status_kis_demo
                }
                val actions = buildList<Pair<Int, () -> Unit>> {
                    add(R.string.account_login to { showLogin(hasSavedKey = status?.hasKey == true) })
                    if (connected) add(R.string.account_logout to ::confirmLogout)
                    add(R.string.account_about to ::showAbout)
                }
                MaterialAlertDialogBuilder(this)
                    .setTitle(title)
                    .setItems(actions.map { getString(it.first) }.toTypedArray()) { _, index -> actions[index].second() }
                    .setNegativeButton(R.string.close, null)
                    .show()
            }
        }
    }

    private fun showLogin(hasSavedKey: Boolean) {
        val form = layoutInflater.inflate(R.layout.dialog_login, null)
        val keyField = form.findViewById<TextInputEditText>(R.id.login_app_key)
        val secretField = form.findViewById<TextInputEditText>(R.id.login_app_secret)
        val status = form.findViewById<TextView>(R.id.login_status)
        form.findViewById<View>(R.id.login_saved_hint).isVisible = hasSavedKey

        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle(R.string.login_title)
            .setView(form)
            .setPositiveButton(R.string.login_submit, null)  // 눌러도 닫히지 않게 아래에서 직접 처리합니다
            .setNegativeButton(R.string.cancel, null)
            .create()
        // 키를 입력하는 창은 화면 캡처와 최근 앱 미리보기에 남지 않게 합니다.
        dialog.window?.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        dialog.setOnShowListener {
            val submit = dialog.getButton(DialogInterface.BUTTON_POSITIVE)
            val cancel = dialog.getButton(DialogInterface.BUTTON_NEGATIVE)
            fun setBusy(busy: Boolean) {
                listOf(keyField, secretField, submit, cancel).forEach { it.isEnabled = !busy }
                dialog.setCancelable(!busy)
            }
            submit.setOnClickListener {
                val appKey = keyField.text?.toString().orEmpty()
                val appSecret = secretField.text?.toString().orEmpty()
                setBusy(true)
                status.setTextColor(getColor(R.color.chart_text_muted))
                status.setText(R.string.login_checking)
                worker.execute {
                    val error = runCatching { ChartServer.login(this, appKey, appSecret) }
                        .getOrElse {
                            Log.e(TAG, "한투 연결 처리 실패 (${it.javaClass.simpleName})")
                            getString(R.string.login_failed)
                        }
                    ui.post {
                        if (!dialog.isShowing) return@post
                        if (error.isEmpty()) {
                            dialog.dismiss()
                            reloadChart()
                        } else {
                            setBusy(false)
                            status.setTextColor(getColor(R.color.chart_error))
                            status.text = error
                        }
                    }
                }
            }
        }
        dialog.show()
    }

    private fun confirmLogout() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.logout_title)
            .setMessage(R.string.logout_message)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.account_logout) { _, _ ->
                worker.execute {
                    val error = runCatching { ChartServer.logout(this) }
                        .getOrElse {
                            Log.e(TAG, "로그아웃 처리 실패 (${it.javaClass.simpleName})")
                            getString(R.string.logout_failed)
                        }
                    ui.post {
                        if (isDestroyed) return@post
                        reloadChart()
                        if (error.isNotEmpty()) Toast.makeText(this, error, Toast.LENGTH_LONG).show()
                    }
                }
            }
            .show()
    }

    private fun showAbout() {
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.app_name)
            .setMessage(getString(R.string.about_message, BuildConfig.VERSION_NAME, BuildConfig.DESKTOP_REVISION))
            .setPositiveButton(R.string.close, null)
            .show()
    }

    private companion object {
        const val TAG = "KisTradingView"
        const val BRIDGE = "KisAndroidBridge"
    }
}
