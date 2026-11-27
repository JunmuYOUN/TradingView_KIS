"""Windows 데스크톱 창에서 KIS 차트를 실행합니다."""
from __future__ import annotations

import ctypes
import asyncio
import logging
import socket
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from threading import Lock, Thread
from time import monotonic, sleep

import uvicorn
import webview
from webview.menu import Menu, MenuAction

from desktop_login import load_startup_settings, logout_kis, restore_desktop_session, show_login
from server.config import DATA_DIR, WEB_DIR


TITLE = "KIS TradingView"
HOST = "127.0.0.1"
PORT = 8765  # Local storage in WebView2 needs the same origin across launches.
URL = f"http://{HOST}:{PORT}"
LOG_FILE = DATA_DIR / "desktop.log"
log = logging.getLogger("desktop")
STARTUP_HTML = """<!doctype html><html lang="ko"><meta charset="utf-8">
<style>html{color-scheme:dark;background:#131722;color:#b2b5be;font:15px 'Malgun Gothic',sans-serif}
body{margin:0;min-height:100vh;display:grid;place-items:center}</style>
<body><p role="status">한투 로그인 상태를 확인하는 중…</p></body></html>"""


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)


def update_account_menu(window, *, signed_in: bool, can_clear_saved: bool = False) -> None:
    from System import Action
    from System.Windows.Forms import MenuStrip

    def update() -> None:
        # pywebview creates the menu strip as a child control of the Windows form.
        strip = next(control for control in window.native.Controls if isinstance(control, MenuStrip))
        items = strip.Items[0].DropDownItems
        items[0].Available = not signed_in
        items[1].Available = signed_in or can_clear_saved

    window.native.Invoke(Action(update))


@contextmanager
def running_server():
    from server.guard import LocalGuard
    from server.main import WS_CLIENT_MAX_MESSAGE, app

    app.state.initial_settings = load_startup_settings()
    app.state.desktop = True
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
            account_action = Lock()
            account_action.acquire()  # 계정 메뉴는 시작 시 로그인 또는 데모 선택이 끝난 뒤 엽니다.

            def load_chart(url: str, *, retry_logout: bool = False) -> None:
                settings = app.state.settings
                update_account_menu(window, signed_in=settings.source == "kis",
                                    can_clear_saved=retry_logout or bool(settings.app_key))
                window.load_url(url)

            def start_chart() -> None:
                window.events.loaded -= start_chart
                try:
                    try:
                        future = asyncio.run_coroutine_threadsafe(restore_desktop_session(app.state), app.state.event_loop)
                        message = future.result()
                    except Exception as exc:
                        log.error("저장된 한투 연결 확인 실패 (%s)", type(exc).__name__)
                        message = "저장된 한투 연결을 확인하지 못했습니다. 다시 로그인하거나 데모로 시작하세요."
                    if message is not None and not window.events.closed.is_set():
                        show_login(window.native, app.state, startup=True, message=message)
                except Exception as exc:
                    if not window.events.closed.is_set():
                        log.error("시작 로그인 화면을 열지 못했습니다 (%s)", type(exc).__name__)
                        show_error("로그인 창을 열지 못해 데모 차트를 엽니다. 계정 메뉴에서 다시 로그인할 수 있습니다.")
                finally:
                    try:
                        if not window.events.closed.is_set():
                            load_chart(launch_url)
                    finally:
                        account_action.release()

            def open_login() -> None:
                if not account_action.acquire(blocking=False):
                    return
                try:
                    if app.state.settings.source != "kis" and show_login(window.native, app.state):
                        load_chart(URL)
                except Exception as exc:
                    log.error("한투 연결 화면을 열지 못했습니다 (%s)", type(exc).__name__)
                    show_error("한투 연결 화면을 열지 못했습니다.")
                finally:
                    account_action.release()

            def logout() -> None:
                if not account_action.acquire(blocking=False):
                    return
                try:
                    future = asyncio.run_coroutine_threadsafe(logout_kis(app.state), app.state.event_loop)
                    error = future.result()
                    load_chart(URL, retry_logout=bool(error))
                    if error:
                        show_error(error)
                except Exception as exc:
                    log.error("로그아웃 처리 실패 (%s)", type(exc).__name__)
                    show_error("로그아웃을 완료하지 못했습니다. 다시 시도하세요.")
                finally:
                    account_action.release()

            window = webview.create_window(
                TITLE, html=STARTUP_HTML, width=1360, height=900, min_size=(800, 550),
                background_color="#131722",
                menu=[
                    Menu("계정", [MenuAction("한투 API 로그인", open_login), MenuAction("로그아웃", logout)]),
                    MenuAction("차트", lambda: window.evaluate_js("document.getElementById('chart-tab')?.click()")),
                    MenuAction("설정", lambda: window.evaluate_js("document.getElementById('settings-tab')?.click()")),
                ],
            )
            window.events.loaded += start_chart
            webview.start(
                gui="edgechromium", private_mode=False, storage_path=str(DATA_DIR / "webview"),
                icon=str(WEB_DIR / "mint-candles.ico"),
            )
    except Exception as exc:
        log.exception("데스크톱 앱을 실행하지 못했습니다")
        show_error(f"TradingView를 실행하지 못했습니다.\n{exc}\n\n로그: {LOG_FILE}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
