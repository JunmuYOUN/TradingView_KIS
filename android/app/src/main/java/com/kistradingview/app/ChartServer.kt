package com.kistradingview.app

import android.content.Context
import com.chaquo.python.PyException
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File

/**
 * 앱 안에서 도는 Python 차트 서버(android_app/bootstrap.py)를 부르는 창구.
 *
 * 모든 함수는 오래 걸릴 수 있으므로 화면(UI) 스레드가 아닌 곳에서 불러야 합니다.
 */
object ChartServer {
    /** 현재 연결 상태. 키 값 자체는 Python 밖으로 나오지 않습니다. */
    data class Status(val source: String, val env: String, val hasKey: Boolean)

    class Failure(message: String, cause: Throwable) : Exception(message, cause)

    private fun bootstrap(context: Context): PyObject {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context.applicationContext))
        }
        return Python.getInstance().getModule("android_app.bootstrap")
    }

    private inline fun <T> call(block: () -> T): T =
        try {
            block()
        } catch (e: PyException) {
            // "RuntimeError: 로컬 포트 …" 에서 사용자에게 보여 줄 문장만 남깁니다.
            throw Failure(e.message.orEmpty().substringAfter(": ").lineSequence().firstOrNull().orEmpty(), e)
        }

    /** 서버를 (필요하면) 시작하고, WebView 가 처음 열 일회용 주소를 돌려줍니다. */
    @Synchronized
    fun launch(context: Context): String = call {
        val dataRoot = File(context.filesDir, "kis").apply { mkdirs() }
        bootstrap(context).callAttr("launch", dataRoot.absolutePath).toString()
    }

    /** 성공하면 빈 문자열, 실패하면 화면에 보여 줄 문구. */
    fun login(context: Context, appKey: String, appSecret: String): String = call {
        bootstrap(context).callAttr("login", appKey, appSecret).toString()
    }

    /** 성공하면 빈 문자열, 실패하면 화면에 보여 줄 문구. */
    fun logout(context: Context): String = call {
        bootstrap(context).callAttr("logout").toString()
    }

    fun status(context: Context): Status = call {
        val json = JSONObject(bootstrap(context).callAttr("status").toString())
        Status(json.getString("source"), json.getString("env"), json.getBoolean("has_key"))
    }
}
