package com.kistradingview.app

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * KIS 키와 토큰을 Android Keystore 의 AES-256-GCM 키로 암호화합니다.
 *
 * 키는 Keystore 안에서 만들어지고 꺼낼 수 없으므로, 암호화한 파일을 복사해 가도 다른 기기나
 * 다시 설치한 앱에서는 풀 수 없습니다. Python(android_app/keystore_vault.py)에서 부르며,
 * 바이트 배열 대신 Base64 문자열을 주고받습니다.
 */
object SecretBox {
    private const val STORE = "AndroidKeyStore"
    private const val ALIAS = "kis_tradingview_vault_v1"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val IV_BYTES = 12
    private const val TAG_BITS = 128

    private fun key(): SecretKey {
        val store = KeyStore.getInstance(STORE).apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val spec = KeyGenParameterSpec.Builder(
            ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build()
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, STORE).run {
            init(spec)
            generateKey()
        }
    }

    /** 평문(Base64)을 암호화해 "IV + 암호문"(Base64)으로 돌려줍니다. IV 는 Keystore 가 매번 새로 만듭니다. */
    @JvmStatic
    @Synchronized
    fun encrypt(plainBase64: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val sealed = cipher.iv + cipher.doFinal(Base64.decode(plainBase64, Base64.NO_WRAP))
        return Base64.encodeToString(sealed, Base64.NO_WRAP)
    }

    /** encrypt 의 결과를 풉니다. 다른 키로 만든 값이거나 변조되었으면 예외를 던집니다. */
    @JvmStatic
    @Synchronized
    fun decrypt(sealedBase64: String): String {
        val sealed = Base64.decode(sealedBase64, Base64.NO_WRAP)
        require(sealed.size > IV_BYTES) { "암호문이 너무 짧습니다" }
        val cipher = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, sealed, 0, IV_BYTES))
        }
        val plain = cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)
        return Base64.encodeToString(plain, Base64.NO_WRAP)
    }
}
