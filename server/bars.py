"""봉(OHLCV) 시간 처리와 타임프레임 집계.

차트 라이브러리는 시간을 UTC 로 표시하므로, 한국 시간(KST)의 '벽시계 시각'을
그대로 UTC epoch 초로 인코딩해서 사용합니다. 예: 2026-11-25 09:00 KST -> timegm(2026,11,25,9,0,0).
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

MINUTE_TFS = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "60": 60}
PERIOD_TFS = {"D", "W", "M"}


def now_kst() -> datetime:
    return datetime.now(KST)


def kst_epoch(yyyymmdd: str, hhmmss: str = "000000") -> int:
    """KST 날짜·시각 문자열을 '벽시계 epoch' 초로 변환합니다."""
    return calendar.timegm((
        int(yyyymmdd[0:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]),
        int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6]), 0, 0, 0,
    ))


def epoch_to_kst_parts(t: int) -> tuple[str, str]:
    d = datetime.fromtimestamp(t, tz=timezone.utc)
    return d.strftime("%Y%m%d"), d.strftime("%H%M%S")


def now_epoch() -> int:
    n = now_kst()
    return kst_epoch(n.strftime("%Y%m%d"), n.strftime("%H%M%S"))


def bucket_start(t: int, tf: str) -> int:
    """시각 t 가 속한 봉의 시작 시각 (클라이언트 js/timeframe.js 와 같은 규칙)."""
    if tf in MINUTE_TFS:
        size = MINUTE_TFS[tf] * 60
        return t - (t % size)
    day = t - (t % 86400)
    if tf == "D":
        return day
    d = datetime.fromtimestamp(day, tz=timezone.utc)
    if tf == "W":
        return day - d.weekday() * 86400  # 월요일
    if tf == "M":
        return calendar.timegm((d.year, d.month, 1, 0, 0, 0, 0, 0, 0))
    raise ValueError(tf)


def aggregate(bars: list[dict], tf: str) -> list[dict]:
    """정렬된 봉 목록을 더 큰 타임프레임으로 묶습니다."""
    out: list[dict] = []
    cur: dict | None = None
    for b in bars:
        s = bucket_start(b["time"], tf)
        if cur is None or cur["time"] != s:
            if cur is not None:
                out.append(cur)
            cur = {"time": s, "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"], "volume": b["volume"]}
        else:
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
            cur["volume"] += b["volume"]
    if cur is not None:
        out.append(cur)
    return out


def dedupe_sort(bars: list[dict]) -> list[dict]:
    m = {b["time"]: b for b in bars}
    return [m[k] for k in sorted(m)]


def tick_size(price: float) -> int:
    """KRX 호가 단위 (2023 개편 기준, 주식)."""
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000
