"""환경 설정 (.env 파일 또는 환경 변수)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    app_key: str
    app_secret: str
    env: str            # "real" | "demo"
    market: str         # "J" (KRX) | "UN" (KRX+NXT 통합) | "NX" (NXT)
    source: str         # "mock" | "kis"
    host: str
    port: int
    rate_per_sec: float

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


def load_settings() -> Settings:
    app_key = os.getenv("KIS_APP_KEY", "").strip()
    app_secret = os.getenv("KIS_APP_SECRET", "").strip()
    env = os.getenv("KIS_ENV", "demo").strip().lower()
    if env not in ("real", "demo"):
        env = "demo"
    market = os.getenv("KIS_MARKET", "J").strip().upper()
    if market not in ("J", "UN", "NX"):
        market = "J"

    source = os.getenv("DATA_SOURCE", "auto").strip().lower()
    if source == "auto":
        source = "kis" if (app_key and app_secret) else "mock"
    if source == "kis" and not (app_key and app_secret):
        print("[config] DATA_SOURCE=kis 이지만 KIS_APP_KEY/KIS_APP_SECRET 이 비어 있어 mock 으로 실행합니다.")
        source = "mock"

    default_rate = 15.0 if env == "real" else 4.0
    try:
        rate = float(os.getenv("KIS_RATE_PER_SEC", default_rate))
    except ValueError:
        rate = default_rate

    return Settings(
        app_key=app_key,
        app_secret=app_secret,
        env=env,
        market=market,
        source=source,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        rate_per_sec=max(0.5, rate),
    )
