"""Windows DPAPI 로 비밀 값(KIS 키, 접근토큰, 웹소켓 접속키)을 암호화해 파일에 저장합니다.

DPAPI 는 현재 Windows 사용자 계정의 키로 암호화합니다. 파일을 복사해 가도 다른 계정이나
다른 PC 에서는 풀 수 없습니다. Windows 가 아니면 디스크에 쓰지 않고(메모리에서만 사용),
읽기는 항상 None 을 돌려줍니다.
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import sys
from pathlib import Path

AVAILABLE = sys.platform == "win32"
MAGIC = b"KTV1"
_ENTROPY = b"KIS TradingView local secret v1"
_DESCRIPTION = "KIS TradingView"
_UI_FORBIDDEN = 0x01

if AVAILABLE:
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _BlobPtr = ctypes.POINTER(_Blob)
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = (_BlobPtr, wintypes.LPCWSTR, _BlobPtr, ctypes.c_void_p,
                                          ctypes.c_void_p, wintypes.DWORD, _BlobPtr)
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = (_BlobPtr, ctypes.c_void_p, _BlobPtr, ctypes.c_void_p,
                                            ctypes.c_void_p, wintypes.DWORD, _BlobPtr)
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(buffer) -> "_Blob":
    return _Blob(len(buffer), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _transform(data: bytes, encrypt: bool) -> bytes:
    if not AVAILABLE:
        raise OSError("Windows DPAPI 를 사용할 수 없습니다")
    source = ctypes.create_string_buffer(data, len(data))
    entropy = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    result = _Blob()
    try:
        if encrypt:
            ok = _crypt32.CryptProtectData(ctypes.byref(_blob(source)), _DESCRIPTION, ctypes.byref(_blob(entropy)),
                                           None, None, _UI_FORBIDDEN, ctypes.byref(result))
        else:
            ok = _crypt32.CryptUnprotectData(ctypes.byref(_blob(source)), None, ctypes.byref(_blob(entropy)),
                                             None, None, _UI_FORBIDDEN, ctypes.byref(result))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.memset(result.pbData, 0, result.cbData)
            _kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))
    finally:
        ctypes.memset(source, 0, len(data))  # 복호화한 평문 사본을 지웁니다 (최선의 노력)


def protect(data: bytes) -> bytes:
    return _transform(data, encrypt=True)


def unprotect(data: bytes) -> bytes:
    return _transform(data, encrypt=False)


def save(path: Path, value: dict) -> bool:
    """value 를 암호화해 path 에 원자적으로 씁니다. Windows 가 아니면 쓰지 않고 False 를 돌려줍니다."""
    if not AVAILABLE:
        return False
    blob = MAGIC + protect(json.dumps(value, separators=(",", ":")).encode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(pending, "xb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    return True


def load(path: Path) -> dict | None:
    """save() 로 쓴 파일을 읽습니다. 없거나, 다른 계정의 파일이거나, 손상되었으면 None."""
    if not AVAILABLE:
        return None
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    if not blob.startswith(MAGIC):
        return None
    try:
        value = json.loads(unprotect(blob[len(MAGIC):]))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
