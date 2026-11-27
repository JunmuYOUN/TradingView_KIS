"""Yahoo Finance chart transport for the mobile Python runtime."""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .bars import aggregate
from .yahoo_source import HISTORY_START, YahooError, chart_bar


_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


async def load_chart(ticker: str, interval: str, start: int | None, end: int | None) -> list[dict]:
    # Yahoo's chart service can return hourly rows to a 30-minute request.
    native = "15m" if interval == "30m" else interval
    params = {
        "interval": native, "period1": HISTORY_START if start is None else start,
        "period2": int(time.time()) if end is None else end, "includePrePost": "false",
    }
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(ticker, safe="")
    try:
        async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "KIS-Chart/1.0"}) as client:
            response = await client.get(url, params=params)
    except httpx.RequestError:
        raise YahooError("Yahoo Finance에 연결할 수 없습니다. 잠시 후 다시 시도하세요.") from None
    if response.status_code == 429:
        raise YahooError("Yahoo Finance의 조회 한도에 도달했습니다. 잠시 후 다시 시도하세요.", status_code=429)
    try:
        chart = response.json()["chart"]
        if response.status_code == 422:
            detail = str((chart.get("error") or {}).get("description", "")).lower()
            if "no data" in detail or "within the last" in detail:
                return []
        response.raise_for_status()
        result = chart["result"][0]
        timestamps = result.get("timestamp") or []
        if not timestamps:
            return []
        tz = ZoneInfo(result["meta"]["exchangeTimezoneName"])
        values = result["indicators"]["quote"][0]
        prices = [values[field] for field in ("open", "high", "low", "close")]
        volumes = values.get("volume") or []
        bars = {}
        step = int(native[:-1]) * 60 if native.endswith("m") else 86400
        previous_raw = None
        previous_wall = None
        for i, stamp in enumerate(timestamps):
            local = (_UTC_EPOCH + timedelta(seconds=stamp)).astimezone(tz)
            wall = calendar.timegm(local.timetuple())
            if native == "1d":
                wall -= wall % 86400
            else:
                wall -= wall % 60
            bar = chart_bar(wall, *(series[i] for series in prices), volumes[i] if i < len(volumes) else 0)
            if bar is None:
                continue
            if (native != "1d" and previous_raw is not None and i == len(timestamps) - 1
                    and previous_wall // 86400 == wall // 86400 and 0 < stamp - previous_raw < step):
                # The extra timestamp is an update to the current exchange bar. Its volume can
                # already include the normal row, so adding the two would count trades twice.
                current = bars[previous_wall]
                current["high"] = max(current["high"], bar["high"], bar["close"])
                current["low"] = min(current["low"], bar["low"], bar["close"])
                current["close"] = bar["close"]
                current["volume"] = max(current["volume"], bar["volume"])
            else:
                bars[wall] = bar
            previous_raw, previous_wall = stamp, wall
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, ZoneInfoNotFoundError, httpx.HTTPStatusError):
        raise YahooError("Yahoo Finance에서 차트 데이터를 받지 못했습니다. 잠시 후 다시 시도하세요.") from None
    ordered = [bars[t] for t in sorted(bars)]
    return aggregate(ordered, "30") if interval == "30m" else ordered
