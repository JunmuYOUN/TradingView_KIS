"""종목 코드 형식.

- 국내: 6자리 단축코드 (005930, 0088M0)
- 해외(미국): 거래소:티커 (NAS:AAPL, NYS:KO, AMS:SPY). 거래소 코드는 한국투자증권 API 의 EXCD 입니다.
- 지수: INDEX:업종코드 (INDEX:0001) 또는 INDEX:미국지수심볼 (INDEX:SPX).
- Yahoo Finance: YF:공개 티커 (YF:ES=F, YF:KRW=X, YF:^VIX).
"""
from __future__ import annotations

import re

from .yahoo_symbols import YAHOO_SYMBOLS

DOMESTIC_RE = re.compile(r"^[0-9A-Z]{6}$")
OVERSEAS_RE = re.compile(r"^(NAS|NYS|AMS):([A-Z0-9][A-Z0-9.\-]{0,15})$")
EXCHANGES = {"NAS": "NASDAQ", "NYS": "NYSE", "AMS": "AMEX"}
INDEX_RE = re.compile(r"INDEX:(?:[0-9]{4}|(?=.{1,10}\Z)\.?[A-Z][A-Z0-9]*(?:[.\-][A-Z0-9]+)*)")
YAHOO_RE = re.compile(r"(?:[A-Z0-9]{1,12}=F|(?:[A-Z]{3}|[A-Z]{6})=X|\^[A-Z0-9]+(?:[.\-][A-Z0-9]+)*)")


def normalize(code) -> str | None:
    """올바른 종목 코드면 대문자로 정리해 돌려주고, 아니면 None."""
    value = str(code or "").strip()
    if not value.isascii():
        return None
    c = value.upper()
    return c if DOMESTIC_RE.fullmatch(c) or OVERSEAS_RE.fullmatch(c) or is_index(c) or is_yahoo(c) else None


def yahoo_candidate(text: str) -> str | None:
    """공개 Yahoo 티커를 직접 입력했을 때만 후보를 반환합니다. 실재 여부는 종목 목록에서 확인합니다."""
    if not isinstance(text, str) or not text.isascii():
        return None
    raw = text.strip().upper().removeprefix("YF:")
    if not 1 <= len(raw) <= 24:
        return None
    code = "YF:" + raw
    return code if code in YAHOO_SYMBOLS or YAHOO_RE.fullmatch(raw) else None


def is_yahoo(code: str) -> bool:
    return isinstance(code, str) and code.startswith("YF:") and yahoo_candidate(code) == code


def yahoo_ticker(code: str) -> str:
    if not is_yahoo(code):
        raise ValueError("지원하지 않는 Yahoo Finance 코드입니다")
    return code[3:]


def is_overseas(code: str) -> bool:
    return OVERSEAS_RE.fullmatch(code) is not None


def is_index(code: str) -> bool:
    return INDEX_RE.fullmatch(code) is not None


def index_kis_code(code: str) -> str:
    if not is_index(code):
        raise ValueError("지수 코드 형식이 올바르지 않습니다")
    return code.removeprefix("INDEX:")


def is_us_index(code: str) -> bool:
    return is_index(code) and not code.removeprefix("INDEX:").isdigit()


def split_overseas(code: str) -> tuple[str, str]:
    """'NAS:AAPL' -> ('NAS', 'AAPL')"""
    excd, symb = code.split(":", 1)
    return excd, symb


def ticker(code: str) -> str:
    """화면에 보여 줄 단축코드나 티커."""
    return code.rsplit(":", 1)[-1]


def file_stem(code: str) -> str:
    """파일 이름에 쓸 수 있게 바꿉니다 (Windows 는 ':' 를 쓸 수 없습니다)."""
    canonical = normalize(code)
    if canonical is None:
        raise ValueError("지원하지 않는 종목 코드입니다")
    return canonical.replace(":", "_")
