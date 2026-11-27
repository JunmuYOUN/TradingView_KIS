"""Windows 데스크톱 창에서 KIS 차트를 실행합니다."""
from __future__ import annotations

import ctypes
import logging
import socket
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from threading import Thread
from time import monotonic, sleep

import uvicorn
import webview
from webview.menu import Menu, MenuAction

from desktop_login import load_desktop_settings, show_login
from server.config import DATA_DIR


TITLE = "KIS TradingView"
HOST = "127.0.0.1"
PORT = 8765  # Local storage in WebView2 needs the same origin across launches.
URL = f"http://{HOST}:{PORT}"
LOG_FILE = DATA_DIR / "desktop.log"
log = logging.getLogger("desktop")


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)


@contextmanager
def running_server():
    from server.guard import LocalGuard
    from server.main import WS_CLIENT_MAX_MESSAGE, app

    app.state.initial_settings = load_desktop_settings()
    # 이 실행에서만 쓰는 세션 쿠키를 앱 창에만 줍니다. 다른 사이트·다른 사용자의 접속은 거부됩니다.
    guard = LocalGuard(app, host=HOST, port=PORT, require_session=True)
    config = uvicorn.Config(
        guard,
        loop="asyncio",
        http="h11",
        ws="websockets",
        ws_max_size=WS_CLIENT_MAX_MESSAGE,
        lifespan="on",
        log_config=None,
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((HOST, PORT))
        except OSError as exc:
            raise RuntimeError(f"TradingView가 이미 열려 있거나 로컬 포트 {PORT}가 사용 중입니다.") from exc

        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = monotonic() + 10
            while not server.started and worker.is_alive() and monotonic() < deadline:
                sleep(0.025)
            if not server.started:
                raise RuntimeError("차트 서버를 시작하지 못했습니다.")
            yield app, guard.launch_url(URL)
        finally:
            server.should_exit = True
            worker.join(timeout=5)


def main() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding="utf-8")],
        )
        from webview.guilib import initialize

        if initialize("edgechromium").renderer != "edgechromium":
            raise RuntimeError("Microsoft Edge WebView2 런타임을 설치한 뒤 다시 실행해 주세요.")

        with running_server() as (app, launch_url):
            def open_login() -> None:
                try:
                    if show_login(window.native, app.state):
                        window.load_url(URL)
                except Exception as exc:
                    log.error("한투 연결 화면을 열지 못했습니다 (%s)", type(exc).__name__)
                    show_error("한투 연결 화면을 열지 못했습니다.")

            window = webview.create_window(
                TITLE, launch_url, width=1360, height=900, min_size=(800, 550),
                background_color="#131722",
                menu=[Menu("설정", [MenuAction("한투 API 연결", open_login)])],
            )
            webview.start(gui="edgechromium", private_mode=False, storage_path=str(DATA_DIR / "webview"))
    except Exception as exc:
        log.exception("데스크톱 앱을 실행하지 못했습니다")
        show_error(f"TradingView를 실행하지 못했습니다.\n{exc}\n\n로그: {LOG_FILE}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
