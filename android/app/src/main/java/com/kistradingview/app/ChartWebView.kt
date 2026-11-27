package com.kistradingview.app

import android.content.Context
import android.util.AttributeSet
import android.view.View
import android.webkit.WebView

/**
 * 얼러트를 감시하는 동안에는 앱이 화면에서 벗어나도 페이지가 "보이는 중"이라고 여기게 하는 WebView.
 *
 * WebView 는 창이 가려지면 페이지를 숨김 상태로 바꾸고, 숨겨진 페이지의 타이머는 점점 느려져
 * 몇 분 뒤에는 1분에 한 번만 돕니다. 그러면 5초마다 하는 시세 갱신과 얼러트 판정이 늦어집니다.
 */
class ChartWebView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : WebView(context, attrs) {

    var stayVisible = false
        set(value) {
            if (field == value) return
            field = value
            // 감시를 끝낼 때, 그동안 전하지 않은 "가려짐" 상태를 뒤늦게 전합니다.
            if (!value && windowVisibility != View.VISIBLE) super.onWindowVisibilityChanged(windowVisibility)
        }

    override fun onWindowVisibilityChanged(visibility: Int) {
        if (stayVisible && visibility != View.VISIBLE) return
        super.onWindowVisibilityChanged(visibility)
    }
}
