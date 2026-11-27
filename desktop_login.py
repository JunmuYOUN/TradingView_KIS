"""한국투자증권 Open API 연결을 위한 Windows 대화상자."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import httpx

from server.config import (
    Settings, forget_connection, load_settings, load_stored_connection, migrate_plaintext_keys,
    save_stored_connection,
)
from server.mock_source import MockSource
from server.kis_source import KisCredentialError, KisError, KisSource


log = logging.getLogger("desktop.login")


def load_desktop_settings() -> Settings:
    migrate_plaintext_keys()
    stored = load_stored_connection()
    overrides = {"DATA_SOURCE": "auto", **stored} if stored else {}
    # 이전 로그인 창의 시장 대신 설정 탭에서 저장한 거래소를 사용합니다. 첫 기본값은 KRX입니다.
    return load_settings({**overrides, "KIS_MARKET": "J"})


def load_startup_settings() -> Settings:
    settings = load_desktop_settings()
    if settings.source == "kis":
        return replace(settings, source="yahoo")
    return replace(settings, source="yahoo", app_key="", app_secret="")


async def restore_desktop_session(state) -> str | None:
    """Return None when connected, or the initial message for the startup login form."""
    current = state.settings
    if not current.app_key or not current.app_secret:
        return ""
    error = await connect_kis(state, current.app_key, current.app_secret, restore_saved=True)
    return error or None


async def logout_kis(state) -> str:
    """End this app's KIS session, including credentials for automatic login."""
    async with state.connection_lock:
        previous = state.source
        guest = state.yahoo if getattr(state, "desktop", False) else MockSource()
        settings = replace(state.settings, source=guest.name, app_key="", app_secret="")
        state.source, state.settings = guest, settings
        state.initial_settings = settings
        symbols = getattr(state, "symbols", None)
        if symbols is not None:
            symbols.download = guest.name == "yahoo" and settings.symbol_download
            if symbols.download:
                symbols.refresh_if_stale()
            else:
                await symbols.close()
        try:
            if previous is not guest:
                await previous.close()
        except Exception as exc:
            log.warning("시세 연결 종료 실패 (%s)", type(exc).__name__)
        try:
            forget_connection()
        except OSError:
            return "현재 연결은 종료했습니다. 저장한 인증정보를 모두 지우지 못했습니다. 폴더 권한을 확인하고 로그아웃을 다시 누르세요."
    return ""


async def connect_kis(state, app_key: str, app_secret: str, *, restore_saved: bool = False) -> str:
    """Validate, save and activate a KIS connection, or return a message for the form."""
    app_key, app_secret = app_key.strip(), app_secret.strip()
    if not app_key and not app_secret:
        app_key, app_secret = state.settings.app_key, state.settings.app_secret
    if not app_key or not app_secret:
        return "App Key와 App Secret을 모두 입력하세요."
    if any(char.isspace() or ord(char) < 32 for char in app_key + app_secret):
        return "키에 공백이나 줄바꿈이 있는지 확인하세요."
    async with state.connection_lock:
        first_env = state.settings.env
        environments = (first_env,) if restore_saved else (first_env, "real" if first_env == "demo" else "demo")
        for env in environments:
            values = {"KIS_APP_KEY": app_key, "KIS_APP_SECRET": app_secret, "KIS_ENV": env,
                      "KIS_MARKET": state.settings.market}
            try:
                settings = load_settings({**values, "DATA_SOURCE": "auto"})
                source = KisSource(settings)
            except OSError:
                return "캐시 폴더에 쓸 수 없습니다. 실행 파일이 있는 폴더의 권한을 확인하세요."
            previous = state.source
            try:
                # A cached token alone cannot tell us if the broker still accepts the keys.
                # A single read-only quote also validates reused tokens without reissuing them.
                await asyncio.wait_for(source.rest.verify_session(), timeout=6 if restore_saved else 20)
                if not restore_saved:
                    save_stored_connection(values)
                state.settings, state.source, state.initial_settings = settings, source, settings
                symbols = getattr(state, "symbols", None)
                if symbols is not None:
                    symbols.download = settings.symbol_download
                    symbols.refresh_if_stale()
                return ""
            except KisCredentialError:
                continue
            except KisError as exc:
                return f"한투 연결을 확인하지 못했습니다 ({exc.code}). 잠시 후 다시 시도하세요."
            except (httpx.RequestError, TimeoutError, ValueError):
                return "한투 서버에서 응답을 받지 못했습니다. 잠시 후 다시 시도하세요."
            except OSError:
                return "키를 암호화해 저장하지 못했습니다. 실행 파일이 있는 폴더의 권한을 확인하세요."
            finally:
                inactive = previous if state.source is source else source
                try:
                    if inactive is not getattr(state, "yahoo", None):
                        await inactive.close()
                except Exception as exc:
                    log.warning("이전 데이터 연결 정리 실패 (%s)", type(exc).__name__)
    return "한투 인증에 실패했습니다. App Key와 App Secret을 확인하세요."


def show_login(owner, state, *, startup: bool = False, message: str = "") -> bool:
    from System import Action

    accepted = False

    def show() -> None:
        nonlocal accepted
        accepted = _show_dialog(owner, state, startup=startup, message=message)

    owner.Invoke(Action(show))
    return accepted


def _show_dialog(owner, state, *, startup: bool = False, message: str = "") -> bool:
    from System.Drawing import Color, Point, Size
    from System.Windows import Forms as ui

    form = ui.Form()
    form.Icon = owner.Icon
    form.Text = "한투 API 로그인"
    form.ClientSize = Size(478, 287)
    form.AutoScaleMode = ui.AutoScaleMode.Dpi
    form.FormBorderStyle = ui.FormBorderStyle.FixedDialog
    form.StartPosition = ui.FormStartPosition.CenterParent
    form.MaximizeBox = form.MinimizeBox = form.ShowInTaskbar = False

    def place(control, x: int, y: int, width: int, height: int):
        control.Location = Point(x, y)
        control.Size = Size(width, height)
        form.Controls.Add(control)
        return control

    def label(text: str, x: int, y: int, width: int, height: int = 20):
        control = ui.Label()
        control.Text = text
        return place(control, x, y, width, height)

    def field(text: str, y: int, secret: bool = False):
        label(text, 24, y, 430)
        control = ui.TextBox()
        control.MaxLength = 512
        control.UseSystemPasswordChar = secret
        return place(control, 24, y + 22, 430, 26)

    label("KIS Developers에서 발급받은 키를 입력하세요.", 24, 22, 430)
    current = state.settings
    if current.app_key and current.app_secret:
        label("저장된 키로 다시 로그인하려면 두 입력칸을 비워 두세요.", 24, 46, 430).ForeColor = Color.DimGray
    elif startup:
        label("KIS API 없이 사용하면 Yahoo Finance 시세로 차트를 엽니다.", 24, 46, 430).ForeColor = Color.DimGray

    key = field("App Key", 75, secret=True)
    secret = field("App Secret", 131, secret=True)
    status = label(message or "키와 토큰은 이 Windows 계정에서만 풀 수 있게 암호화해 저장합니다.", 24, 192, 430, 42)
    status.ForeColor = Color.Firebrick if message else Color.DimGray
    submit = ui.Button()
    submit.Text = "로그인"
    cancel = ui.Button()
    cancel.Text = "KIS API 없이 사용" if startup else "취소"
    cancel.DialogResult = ui.DialogResult.Cancel
    place(submit, 200, 239, 96, 30)
    place(cancel, 304, 239, 150, 30)
    form.AcceptButton, form.CancelButton = submit, cancel

    timer = ui.Timer()
    timer.Interval = 80
    pending = None
    fields = (key, secret, submit, cancel)

    def submit_connection(_sender, _event) -> None:
        nonlocal pending
        if pending is not None:
            return
        pending = asyncio.run_coroutine_threadsafe(
            connect_kis(state, key.Text, secret.Text),
            state.event_loop,
        )
        for control in fields:
            control.Enabled = False
        status.ForeColor = Color.DimGray
        status.Text = "한투 API 인증을 확인하고 있습니다…"
        timer.Start()

    def check_connection(_sender, _event) -> None:
        nonlocal pending
        if pending is None or not pending.done():
            return
        timer.Stop()
        try:
            error = pending.result()
        except Exception as exc:
            log.error("한투 설정 처리 실패 (%s)", type(exc).__name__)
            error = "연결을 완료하지 못했습니다. 잠시 후 다시 시도하세요."
        pending = None
        if not error:
            form.DialogResult = ui.DialogResult.OK
            return
        for control in fields:
            control.Enabled = True
        status.ForeColor = Color.Firebrick
        status.Text = error
        key.Focus()

    def prevent_close_while_connecting(_sender, event) -> None:
        if pending is not None:
            event.Cancel = True

    submit.Click += submit_connection
    timer.Tick += check_connection
    form.FormClosing += prevent_close_while_connecting
    try:
        return form.ShowDialog(owner) == ui.DialogResult.OK
    finally:
        timer.Dispose()
        form.Dispose()
