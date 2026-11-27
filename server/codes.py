"""종목 코드 형식.

- 국내: 6자리 단축코드 (005930, 0088M0)
- 해외(미국): 거래소:티커 (NAS:AAPL, NYS:KO, AMS:SPY). 거래소 코드는 한국투자증권 API 의 EXCD 입니다.
"""
from __future__ import annotations

import re

DOMESTIC_RE = re.compile(r"^[0-9A-Z]{6}$")
OVERSEAS_RE = re.compile(r"^(NAS|NYS|AMS):([A-Z0-9][A-Z0-9.\-]{0,15})$")
EXCHANGES = {"NAS": "NASDAQ", "NYS": "NYSE", "AMS": "AMEX"}


def normalize(code) -> str | None:
    """올바른 종목 코드면 대문자로 정리해 돌려주고, 아니면 None."""
    c = str(code or "").strip().upper()
    return c if DOMESTIC_RE.match(c) or OVERSEAS_RE.match(c) else None


def is_overseas(code: str) -> bool:
    return ":" in code


def split_overseas(code: str) -> tuple[str, str]:
    """'NAS:AAPL' -> ('NAS', 'AAPL')"""
    excd, symb = code.split(":", 1)
    return excd, symb


def ticker(code: str) -> str:
    """화면에 보여 줄 코드: 국내는 그대로, 해외는 티커만."""
    return code.split(":", 1)[1] if is_overseas(code) else code


def file_stem(code: str) -> str:
    """파일 이름에 쓸 수 있게 바꿉니다 (Windows 는 ':' 를 쓸 수 없습니다)."""
    return code.replace(":", "_")
