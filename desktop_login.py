"""한국투자증권 Open API 연결을 위한 Windows 대화상자."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets

import httpx
from dotenv import dotenv_values

from server.config import ENV_FILE, Settings, load_settings, load_stored_connection, save_stored_connection
from server.kis_source import KisError, KisSource


log = logging.getLogger("desktop.login")
ENVIRONMENTS = {"demo": "모의투자", "real": "실전투자"}
MARKETS = {"J": "KRX", "UN": "KRX + NXT 통합", "NX": "NXT"}
# .env 의 키 줄 (값만 지우고 줄과 주석은 남깁니다)
SECRET_LINE = re.compile(rb"^([ \t]*(?:export[ \t]+)?KIS_APP_(?:KEY|SECRET)[ \t]*=)[^\r\n]*", re.M)


def migrate_plaintext_keys() -> None:
    """이전 버전이 .env 에 평문으로 저장한 키를 암호화 저장소로 옮기고 .env 에서는 지웁니다."""
    if not ENV_FILE.exists():
        return
    values = dotenv_values(ENV_FILE, interpolate=False)
    app_key = (values.get("KIS_APP_KEY") or "").strip()
    app_secret = (values.get("KIS_APP_SECRET") or "").strip()
    if not (app_key and app_secret):
        return
    save_stored_connection({"KIS_APP_KEY": app_key, "KIS_APP_SECRET": app_secret,
                            "KIS_ENV": values.get("KIS_ENV") or "demo",
                            "KIS_MARKET": values.get("KIS_MARKET") or "J"})
    stored = load_stored_connection()
    if not stored or (stored["KIS_APP_KEY"], stored["KIS_APP_SECRET"]) != (app_key, app_secret):
        raise OSError("암호화한 키를 다시 읽지 못했습니다")  # 확인되기 전에는 .env 를 건드리지 않습니다
    pending = ENV_FILE.with_name(f".env.{secrets.token_hex(4)}.tmp")
    try:
        pending.write_bytes(SECRET_LINE.sub(rb"\1", ENV_FILE.read_bytes()))
        os.replace(pending, ENV_FILE)
    finally:
        pending.unlink(missing_ok=True)
    log.info(".env 에 있던 평문 키를 암호화 저장소로 옮겼습니다")


def load_desktop_settings() -> Settings:
    try:
        migrate_plaintext_keys()
    except (OSError, ValueError) as exc:
        log.warning(".env 의 평문 키를 암호화 저장소로 옮기지 못했습니다 (%s)", type(exc).__name__)
    stored = load_stored_connection()
    # 앱에서 연결한 키(암호화 저장소)가 .env·환경 변수보다 우선합니다
    return load_settings({**stored, "DATA_SOURCE": "auto"} if stored else None)


async def connect_kis(state, app_key: str, app_secret: str, env: str, market: str) -> str:
    """Validate, save and activate a KIS connection, or return a message for the form."""
    app_key, app_secret = app_key.strip(), app_secret.strip()
    if not app_key and not app_secret:
        app_key, app_secret = state.settings.app_key, state.settings.app_secret
    if not app_key or not app_secret:
        return "App Key와 App Secret을 모두 입력하세요."
    if any(char.isspace() or ord(char) < 32 for char in app_key + app_secret):
        return "키에 공백이나 줄바꿈이 있는지 확인하세요."
    if env not in ENVIRONMENTS or market not in MARKETS:
        return "투자 환경과 시장을 선택하세요."

    values = {"KIS_APP_KEY": app_key, "KIS_APP_SECRET": app_secret, "KIS_ENV": env, "KIS_MARKET": market}
    settings = load_settings({**values, "DATA_SOURCE": "auto"})
    previous = state.source
    try:
        source = KisSource(settings)
    except OSError:
        return "캐시 폴더에 쓸 수 없습니다. 실행 파일이 있는 폴더의 권한을 확인하세요."
    try:
        await asyncio.wait_for(source.rest.auth.token(), timeout=20)
        save_stored_connection(values)
    except KisError:
        return "한투 인증에 실패했습니다. 발급받은 키와 모의·실전 구분을 확인하세요."
    except (httpx.RequestError, TimeoutError, ValueError):
        return "한투 인증 서버에서 응답을 받지 못했습니다. 잠시 후 다시 시도하세요."
    except OSError:
        return "키를 암호화해 저장하지 못했습니다. 실행 파일이 있는 폴더의 권한을 확인하세요."
    else:
        state.settings, state.source = settings, source
        return ""
    finally:
        inactive = previous if state.source is source else source
        try:
            await inactive.close()
        except Exception as exc:
            log.warning("이전 데이터 연결 정리 실패 (%s)", type(exc).__name__)


def show_login(owner, state) -> bool:
    from System import Action

    accepted = False

    def show() -> None:
        nonlocal accepted
        accepted = _show_dialog(owner, state)

    owner.Invoke(Action(show))
    return accepted


def _show_dialog(owner, state) -> bool:
    from System.Drawing import Color, Point, Size
    from System.Windows import Forms as ui

    form = ui.Form()
    form.Text = "한투 API 연결"
    form.ClientSize = Size(478, 354)
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

    def options(text: str, x: int, choices: dict[str, str], selected: str):
        label(text, x, 191, 207)
        control = ui.ComboBox()
        control.DropDownStyle = ui.ComboBoxStyle.DropDownList
        for choice in choices.values():
            control.Items.Add(choice)
        control.SelectedIndex = tuple(choices).index(selected)
        return place(control, x, 213, 207, 28)

    label("KIS Developers에서 발급받은 키를 입력하세요.", 24, 22, 430)
    current = state.settings
    if current.app_key and current.app_secret:
        hint = label("저장된 키를 사용하려면 두 입력칸을 비워 두세요.", 24, 46, 430)
        hint.ForeColor = Color.DimGray

    key = field("App Key", 75)
    secret = field("App Secret", 131, secret=True)
    environment = options("투자 환경", 24, ENVIRONMENTS, current.env)
    market = options("시장", 247, MARKETS, current.market)
    status = label("키와 토큰은 이 Windows 계정에서만 풀 수 있게 암호화해 저장합니다.", 24, 255, 430, 44)
    status.ForeColor = Color.DimGray
    submit = ui.Button()
    submit.Text = "연결"
    cancel = ui.Button()
    cancel.Text = "취소"
    cancel.DialogResult = ui.DialogResult.Cancel
    place(submit, 254, 306, 96, 30)
    place(cancel, 358, 306, 96, 30)
    form.AcceptButton, form.CancelButton = submit, cancel

    timer = ui.Timer()
    timer.Interval = 80
    pending = None
    fields = (key, secret, environment, market, submit, cancel)

    def submit_connection(_sender, _event) -> None:
        nonlocal pending
        if pending is not None:
            return
        pending = asyncio.run_coroutine_threadsafe(
            connect_kis(state, key.Text, secret.Text, tuple(ENVIRONMENTS)[environment.SelectedIndex],
                        tuple(MARKETS)[market.SelectedIndex]),
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
