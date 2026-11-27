"""환경 설정 (.env 파일, 환경 변수, 암호화 저장소)."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import vault

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
ENV_FILE = APP_DIR / ".env"
DATA_DIR = APP_DIR / "data"
WEB_DIR = ROOT / "web"

# 데스크톱 앱에서 연결한 KIS 키 (Windows DPAPI 로 암호화)
CREDENTIALS_FILE = DATA_DIR / "kis_credentials.bin"
CREDENTIAL_KEYS = ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ENV", "KIS_MARKET")
# 이전 버전이 토큰을 평문으로 남기던 파일
LEGACY_PLAINTEXT_FILES = tuple(f"kis_{kind}_{env}.json" for kind in ("token", "approval")
                               for env in ("real", "demo"))

load_dotenv(ENV_FILE)


def load_stored_connection() -> dict[str, str] | None:
    """암호화 저장소의 KIS 연결 정보. 없거나 이 Windows 계정에서 풀 수 없으면 None."""
    stored = vault.load(CREDENTIALS_FILE) or {}
    values = {key: stored[key] for key in CREDENTIAL_KEYS if isinstance(stored.get(key), str)}
    if values.get("KIS_APP_KEY") and values.get("KIS_APP_SECRET"):
        return values
    return None


def save_stored_connection(values: Mapping[str, str]) -> None:
    if not vault.save(CREDENTIALS_FILE, {key: values[key] for key in CREDENTIAL_KEYS}):
        raise OSError("이 운영체제에서는 키를 암호화해 저장할 수 없습니다")


def remove_legacy_plaintext() -> None:
    for name in LEGACY_PLAINTEXT_FILES:
        try:
            (DATA_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass


@dataclass(frozen=True)
class Settings:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    env: str            # "real" | "demo"
    market: str         # "J" (KRX) | "UN" (KRX+NXT 통합) | "NX" (NXT)
    source: str         # "mock" | "kis"
    host: str
    port: int
    rate_per_sec: float
    symbol_download: bool = True  # 종목 검색용 마스터 파일을 한국투자증권에서 받을지

    @property
    def rest_base(self) -> str:
        if self.env == "real":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    @property
    def ws_url(self) -> str:
        port = 21000 if self.env == "real" else 31000
        return f"ws://ops.koreainvestment.com:{port}/tryitout"

    @property
    def ws_tr_id(self) -> str:
        # 국내주식 실시간체결가: KRX / 통합 / NXT
        return {"J": "H0STCNT0", "UN": "H0UNCNT0", "NX": "H0NXCNT0"}[self.market]


def load_settings(overrides: Mapping[str, str] | None = None) -> Settings:
    values = dict(os.environ)
    if overrides:
        values.update(overrides)
    elif not (values.get("KIS_APP_KEY", "").strip() and values.get("KIS_APP_SECRET", "").strip()):
        # .env 에 키가 없으면 데스크톱 앱에서 암호화해 저장한 키를 씁니다 (run.bat 에서도)
        values.update(load_stored_connection() or {})
    get = values.get
    app_key = get("KIS_APP_KEY", "").strip()
    app_secret = get("KIS_APP_SECRET", "").strip()
    env = get("KIS_ENV", "demo").strip().lower()
    if env not in ("real", "demo"):
        env = "demo"
    market = get("KIS_MARKET", "J").strip().upper()
    if market not in ("J", "UN", "NX"):
        market = "J"

    source = get("DATA_SOURCE", "auto").strip().lower()
    if source == "auto":
        source = "kis" if (app_key and app_secret) else "mock"
    if source == "kis" and not (app_key and app_secret):
        print("[config] DATA_SOURCE=kis 이지만 KIS_APP_KEY/KIS_APP_SECRET 이 비어 있어 mock 으로 실행합니다.")
        source = "mock"

    default_rate = 15.0 if env == "real" else 4.0
    try:
        rate = float(get("KIS_RATE_PER_SEC", default_rate))
    except ValueError:
        rate = default_rate

    return Settings(
        app_key=app_key,
        app_secret=app_secret,
        env=env,
        market=market,
        source=source,
        host=get("HOST", "127.0.0.1"),
        port=int(get("PORT", "8000")),
        rate_per_sec=max(0.5, rate),
        symbol_download=get("SYMBOL_DOWNLOAD", "auto").strip().lower() not in ("off", "0", "false", "no"),
    )
