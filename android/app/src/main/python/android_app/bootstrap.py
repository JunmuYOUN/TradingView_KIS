"""안드로이드 앱 안에서 데스크톱과 같은 차트 서버를 실행합니다.

server/, web/, desktop_login.py 는 빌드할 때 데스크톱 프로젝트에서 그대로 가져옵니다.
이 모듈은 그 코드를 고치지 않고 안드로이드에 맞게 연결만 합니다.

  - 저장 위치: 앱 전용 저장소 (데스크톱은 실행 파일 옆의 data/)
  - 비밀 값 암호화: Android Keystore (데스크톱은 Windows DPAPI)
  - 접근 제한: 앱의 WebView 에만 세션 쿠키를 주는 LocalGuard (데스크톱과 같음)

데스크톱 코드와 맞닿는 곳은 configure() 와 _start() 두 군데뿐입니다. 데스크톱 쪽 이름이
바뀌면 여기서 바로 알 수 있도록 먼저 검사하고, tools/check_compat.py 가 빌드 전에 같은
경로를 PC 에서 실행해 봅니다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from time import monotonic, sleep

HOST = "127.0.0.1"
PORT = 8765  # WebView 의 localStorage 는 주소가 같아야 유지되므로 데스크톱처럼 고정합니다

log = logging.getLogger("android")

_CONFIG_NAMES = ("APP_DIR", "DATA_DIR", "ENV_FILE", "WEB_DIR", "CREDENTIALS_FILE")
_VAULT_NAMES = ("AVAILABLE", "protect", "unprotect", "save", "load")

_lock = threading.Lock()
_configured = False
_running: "_Running | None" = None


class _Gate:
    """실행 중에도 LocalGuard 를 새것으로 바꿀 수 있게 감싼 ASGI 앱.

    화면(Activity)이 새로 열릴 때마다 일회용 주소를 새로 만들어야 하는데,
    LocalGuard 의 일회용 토큰은 한 번 쓰면 사라지기 때문입니다.
    """

    def __init__(self, guard) -> None:
        self.guard = guard

    async def __call__(self, scope, receive, send) -> None:
        await self.guard(scope, receive, send)


class _Running:
    def __init__(self, app, gate: _Gate, server, thread: threading.Thread) -> None:
        self.app, self.gate, self.server, self.thread = app, gate, server, thread


def base_url() -> str:
    return f"http://{HOST}:{PORT}"


def configure(data_root: Path, protect, unprotect) -> None:
    """데스크톱 서버 코드가 앱 전용 저장소와 주어진 암호화 함수를 쓰게 합니다.

    server.main 은 불러올 때 저장 위치를 읽어 가므로, 반드시 그 전에 실행해야 합니다.
    """
    global _configured
    if _configured:
        return
    if "server.main" in sys.modules:
        raise RuntimeError("server.main 을 불러오기 전에 configure() 를 실행해야 합니다")
    from server import config, vault

    missing = [f"server.config.{name}" for name in _CONFIG_NAMES if not hasattr(config, name)]
    missing += [f"server.vault.{name}" for name in _VAULT_NAMES if not hasattr(vault, name)]
    if missing:
        raise RuntimeError(
            "데스크톱 코드의 구조가 바뀌었습니다. android_app/bootstrap.py 를 맞춰 주세요: " + ", ".join(missing)
        )
    if not (config.WEB_DIR / "index.html").is_file():
        raise RuntimeError(f"화면 파일을 찾지 못했습니다: {config.WEB_DIR}")

    data_dir = data_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    credentials_name = Path(config.CREDENTIALS_FILE).name
    config.APP_DIR = data_root
    config.ENV_FILE = data_root / ".env"
    config.DATA_DIR = data_dir
    config.CREDENTIALS_FILE = data_dir / credentials_name

    vault.AVAILABLE = True
    vault.protect = protect
    vault.unprotect = unprotect
    _configured = True


def _new_guard(app):
    from server.guard import LocalGuard

    return LocalGuard(app, host=HOST, port=PORT, require_session=True)


def _start() -> _Running:
    import uvicorn

    from desktop_login import load_desktop_settings
    from server.main import WS_CLIENT_MAX_MESSAGE, app

    app.state.initial_settings = load_desktop_settings()
    app.state.desktop = False  # 화면의 차트/설정 탭을 그대로 씁니다 (데스크톱은 창 메뉴가 대신함)
    gate = _Gate(_new_guard(app))
    server = uvicorn.Server(uvicorn.Config(
        gate,
        loop="asyncio",
        http="h11",
        ws="websockets",
        ws_max_size=WS_CLIENT_MAX_MESSAGE,
        lifespan="on",
        log_config=None,
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=3,
    ))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":  # Windows 에서는 이 옵션이 다른 프로그램과 포트를 함께 쓰게 만듭니다
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, PORT))
    except OSError as exc:
        listener.close()
        raise RuntimeError(f"로컬 포트 {PORT}를 다른 앱이 사용 중입니다.") from exc

    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, name="chart-server", daemon=True)
    thread.start()
    deadline = monotonic() + 20
    while not server.started and thread.is_alive() and monotonic() < deadline:
        sleep(0.025)
    if not server.started:
        server.should_exit = True
        listener.close()
        raise RuntimeError("차트 서버를 시작하지 못했습니다.")
    return _Running(app, gate, server, thread)


def launch(data_root: str, protect=None, unprotect=None) -> str:
    """서버를 (필요하면) 시작하고, 화면이 처음 열 일회용 주소를 돌려줍니다.

    부를 때마다 세션을 새로 만들기 때문에 이전에 준 주소와 쿠키는 더 통하지 않습니다.
    """
    global _running
    with _lock:
        if protect is None or unprotect is None:
            from . import keystore_vault

            protect, unprotect = keystore_vault.protect, keystore_vault.unprotect
        configure(Path(data_root), protect, unprotect)
        if _running is None or not _running.thread.is_alive():
            _running = _start()
        else:
            _running.gate.guard = _new_guard(_running.app)
        return _running.gate.guard.launch_url(base_url())


def _require_running() -> _Running:
    running = _running
    if running is None or not running.thread.is_alive():
        raise RuntimeError("차트 서버가 실행 중이 아닙니다.")
    return running


def _on_server_loop(make_coroutine, timeout: float) -> str:
    running = _require_running()
    future = asyncio.run_coroutine_threadsafe(make_coroutine(running.app.state), running.app.state.event_loop)
    try:
        return future.result(timeout)
    except FutureTimeout:
        future.cancel()
        return "응답이 너무 오래 걸립니다. 네트워크를 확인하고 다시 시도하세요."


def login(app_key: str, app_secret: str) -> str:
    """KIS 키를 확인하고 저장합니다. 성공하면 빈 문자열, 실패하면 화면에 보여 줄 문구."""
    from desktop_login import connect_kis

    return _on_server_loop(lambda state: connect_kis(state, app_key, app_secret), timeout=90)


def logout() -> str:
    """저장한 키와 토큰을 지우고 MOCK 시세로 돌아갑니다. 성공하면 빈 문자열."""
    from desktop_login import logout_kis

    return _on_server_loop(logout_kis, timeout=30)


def status() -> str:
    """현재 연결 상태 (JSON). 키 값은 넣지 않습니다."""
    settings = _require_running().app.state.settings
    return json.dumps({
        "source": settings.source,
        "env": settings.env,
        "market": settings.market,
        "has_key": bool(settings.app_key and settings.app_secret),
    })


def stop() -> None:
    """서버를 멈춥니다 (검사 도구용. 앱에서는 프로세스와 함께 끝납니다)."""
    global _running
    with _lock:
        running, _running = _running, None
    if running is not None:
        running.server.should_exit = True
        running.thread.join(timeout=10)
