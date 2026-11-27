"""환경 설정 (.env 파일, 환경 변수, 암호화 저장소)."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import dotenv_values
from dotenv.parser import parse_stream

from . import vault

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
ENV_FILE = APP_DIR / ".env"
DATA_DIR = APP_DIR / "data"
WEB_DIR = ROOT / "web"

# 데스크톱 앱에서 연결한 KIS 키 (Windows DPAPI 로 암호화)
CREDENTIALS_FILE = DATA_DIR / "kis_credentials.bin"
CREDENTIAL_KEYS = ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ENV", "KIS_MARKET")
MARKETS = ("J", "NX", "UN")
# 이전 버전이 토큰을 평문으로 남기던 파일
LEGACY_PLAINTEXT_FILES = tuple(f"kis_{kind}_{env}.json" for kind in ("token", "approval")
                               for env in ("real", "demo"))


def load_stored_connection() -> dict[str, str] | None:
    """암호화 저장소의 KIS 연결 정보. 없거나 이 Windows 계정에서 풀 수 없으면 None."""
    stored = vault.load(CREDENTIALS_FILE) or {}
    if stored.get("signed_out") is True:
        return {"KIS_APP_KEY": "", "KIS_APP_SECRET": "", "DATA_SOURCE": "mock"}
    values = {key: stored[key] for key in CREDENTIAL_KEYS if isinstance(stored.get(key), str)}
    if values.get("KIS_APP_KEY") or values.get("KIS_APP_SECRET"):
        return values
    return None


def save_stored_connection(values: Mapping[str, str]) -> None:
    if not vault.save(CREDENTIALS_FILE, {key: values[key] for key in CREDENTIAL_KEYS}):
        raise OSError("이 운영체제에서는 키를 암호화해 저장할 수 없습니다")


def remove_legacy_plaintext() -> None:
    for name in (*LEGACY_PLAINTEXT_FILES, "kis_approval_demo.bin", "kis_approval_real.bin"):
        (DATA_DIR / name).unlink(missing_ok=True)


def migrate_plaintext_keys() -> None:
    """Convert legacy settings before loading credentials into any Windows entry point."""
    if not vault.AVAILABLE or not ENV_FILE.exists():
        return
    with ENV_FILE.open(encoding="utf-8-sig") as file:
        bindings = list(parse_stream(file))
    secret_keys = {"KIS_APP_KEY", "KIS_APP_SECRET"}
    if not any(item.key in secret_keys and item.value for item in bindings):
        return
    values = dotenv_values(ENV_FILE, encoding="utf-8-sig", interpolate=False)
    previous = load_stored_connection() or {}
    connection = {key: values.get(key) or previous.get(key, "") for key in CREDENTIAL_KEYS}
    connection["KIS_ENV"] = connection["KIS_ENV"] or "demo"
    connection["KIS_MARKET"] = connection["KIS_MARKET"] or "J"
    save_stored_connection(connection)
    if load_stored_connection() != connection:
        raise OSError("기존 API 키를 암호화해 보관할 수 없습니다.")
    with NamedTemporaryFile("w", encoding="utf-8", dir=ENV_FILE.parent, prefix=".env.", suffix=".tmp",
                            delete=False) as file:
        pending = Path(file.name)
        try:
            for item in bindings:
                if item.key not in secret_keys:
                    file.write(item.original.string)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            file.close()
            pending.unlink(missing_ok=True)
            raise
    try:
        pending.replace(ENV_FILE)
    finally:
        pending.unlink(missing_ok=True)


def load_market(fallback: str) -> str:
    try:
        market = (DATA_DIR / "domestic_market.txt").read_text(encoding="ascii").strip()
        if market in MARKETS:
            return market
    except (FileNotFoundError, UnicodeError):
        pass
    return fallback if fallback in MARKETS else "J"


def save_market(market: str) -> None:
    if market not in MARKETS:
        raise ValueError("지원하지 않는 거래소입니다.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=DATA_DIR, delete=False) as file:
        pending = Path(file.name)
    try:
        pending.write_text(market, encoding="ascii")
        pending.replace(DATA_DIR / "domestic_market.txt")
    finally:
        pending.unlink(missing_ok=True)


def forget_connection() -> None:
    if not vault.save(CREDENTIALS_FILE, {"signed_out": True}):
        raise OSError("로그아웃 상태를 저장할 수 없습니다.")
    remove_legacy_plaintext()
    for env in ("demo", "real"):
        (DATA_DIR / f"kis_token_{env}.bin").unlink(missing_ok=True)


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
    web_tls_cert: str = ""
    web_tls_key: str = ""
    web_public_host: str = ""

    @property
    def rest_base(self) -> str:
        if self.env == "real":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"


def load_settings(overrides: Mapping[str, str] | None = None) -> Settings:
    migrate_plaintext_keys()
    values = {key: value for key, value in dotenv_values(ENV_FILE, encoding="utf-8-sig").items()
              if value is not None}
    values.update(os.environ)
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
    market = load_market(get("KIS_MARKET", "J").strip().upper())

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
        web_tls_cert=get("WEB_TLS_CERT", "").strip(),
        web_tls_key=get("WEB_TLS_KEY", "").strip(),
        web_public_host=get("WEB_PUBLIC_HOST", "").strip(),
    )
