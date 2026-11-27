"""Android Keystore 로 비밀 값(KIS 키, 접근토큰)을 암호화합니다.

데스크톱의 server/vault.py 는 Windows DPAPI 를 씁니다. 안드로이드에서는 그 자리에 이 모듈의
protect / unprotect 를 끼워 넣습니다. 암호화 키는 Keystore 안에서 만들어지고 기기 밖으로
나가지 않으므로, 파일을 복사해 가도 다른 기기에서는 풀 수 없습니다.
"""
from __future__ import annotations

import base64


def _box():
    from java import jclass  # Chaquopy 가 제공하는 Java 연결

    return jclass("com.kistradingview.app.SecretBox")


def protect(data: bytes) -> bytes:
    try:
        sealed = _box().encrypt(base64.b64encode(data).decode("ascii"))
        return base64.b64decode(str(sealed))
    except Exception as exc:
        raise OSError("Android Keystore 로 암호화하지 못했습니다") from exc


def unprotect(data: bytes) -> bytes:
    try:
        plain = _box().decrypt(base64.b64encode(data).decode("ascii"))
        return base64.b64decode(str(plain))
    except Exception as exc:
        # 앱을 다시 설치했거나 다른 기기의 파일이면 풀 수 없습니다. vault.load 가 None 으로 처리합니다.
        raise OSError("Android Keystore 로 복호화하지 못했습니다") from exc
