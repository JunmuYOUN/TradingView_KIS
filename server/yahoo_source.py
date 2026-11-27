"""Yahoo Finance 선물·환율·지수 시세. 차트 시각은 거래소 현지 벽시계를 UTC 초로 인코딩합니다."""
from __future__ import annotations

import asyncio
import calendar
import math
import sys
import time
from bisect import bisect_left
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from operator import itemgetter

from .bars import MINUTE_TFS, PERIOD_TFS, aggregate, bucket_start, dedupe_sort, kst_epoch
from .codes import is_yahoo
from .yahoo_delay import YahooQuoteStatus

DAY = 86400
REFRESH = 60
HISTORY_START = kst_epoch("18000101")
_MINUTE_RETENTION = {"1": 30, "3": 30, "5": 60, "15": 60, "30": 60, "60": 730}
_Loader = Callable[[str, str, int | None, int | None], Awaitable[list[dict]]]


class YahooError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def chart_bar(stamp: int, opening, high, low, close, volume) -> dict | None:
    try:
        o, h, l, c = map(float, (opening, high, low, close))
    except (TypeError, ValueError):
        return None
    if not all(map(math.isfinite, (o, h, l, c))) or h < l:
        return None
    try:
        v = float(volume)
    except (TypeError, ValueError):
        v = 0.0
    return {"time": stamp, "open": o, "high": h, "low": l, "close": c,
            "volume": v if math.isfinite(v) and v > 0 else 0.0}


def _desktop_chart(ticker: str, interval: str, start: int | None, end: int | None) -> list[dict]:
    import yfinance as yf
    from yfinance.exceptions import YFPricesMissingError, YFRateLimitError

    # yfinance's daily period="max" has a rolling 99-year cutoff.
    options = {"start": HISTORY_START if start is None else start, "end": int(time.time()) if end is None else end}
    try:
        frame = yf.Ticker(ticker).history(interval=interval, actions=False, auto_adjust=False,
                                         raise_errors=True, timeout=10, **options)
    except YFRateLimitError:
        raise YahooError("Yahoo Finance 조회 한도에 도달했습니다. 잠시 후 다시 시도하세요.", 429) from None
    except YFPricesMissingError as exc:
        reason = (exc.yahoo_reason or "").lower()
        if start is not None and ("data doesn't exist" in reason or "requested range must be within" in reason
                                  or (not reason and "status_code" not in exc.debug_info)):
            return []  # 정상 응답이지만 지정한 구간에 봉이 없거나 분봉 보관기간을 벗어났습니다.
        raise YahooError("Yahoo Finance에서 시세 응답을 확인할 수 없습니다.") from None

    bars = []
    for stamp, o, h, l, c, v in frame[["Open", "High", "Low", "Close", "Volume"]].itertuples(name=None):
        wall = calendar.timegm(stamp.timetuple())
        if interval == "1d":
            wall -= wall % DAY
        else:
            # Yahoo가 정규 봉 다음에 붙이는 현재가 행은 초 단위 체결 시각을 가질 수 있습니다.
            wall -= wall % 60
        bar = chart_bar(wall, o, h, l, c, v)
        if bar is not None:
            bars.append(bar)
    return dedupe_sort(bars)


async def _load_chart(ticker, interval, start, end):
    if sys.platform != "android" and not hasattr(sys, "getandroidapilevel"):
        try:
            return await asyncio.to_thread(_desktop_chart, ticker, interval, start, end)
        except ModuleNotFoundError as exc:
            if exc.name != "yfinance":
                raise
    from .yahoo_chart import load_chart
    return await load_chart(ticker, interval, start, end)


class YahooSource:
    name = "yahoo"

    def __init__(self, loader: _Loader | None = None):
        self._loader = loader or _load_chart
        self._status = YahooQuoteStatus()
        self._cache: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()
        self._pending: dict[tuple, asyncio.Task] = {}
        self._network = asyncio.Lock()  # yfinance shares its cookie/session across instances.
        self._retry_at = 0.0
        self._closed = False

    async def _read(self, ticker, interval, start, end) -> list[dict]:
        if not is_yahoo("YF:" + ticker):
            raise YahooError("지원하지 않는 Yahoo Finance 종목입니다.", 404)
        async with self._network:
            if self._closed:
                raise YahooError("시세 연결이 종료되었습니다.", 503)
            if self._retry_at > time.monotonic():
                raise YahooError("Yahoo Finance 조회 한도에 도달했습니다. 잠시 후 다시 시도하세요.", 429)
            try:
                return await self._loader(ticker, interval, start, end)
            except YahooError as exc:
                if exc.status_code == 429:
                    self._retry_at = time.monotonic() + REFRESH
                raise
            except Exception:
                raise YahooError("Yahoo Finance에 연결하지 못했습니다. 잠시 후 다시 시도하세요.") from None

    async def _cached(self, key, ttl, load) -> list[dict]:
        if self._closed:
            raise YahooError("시세 연결이 종료되었습니다.", 503)
        entry = self._cache.get(key)
        if entry and time.monotonic() - entry[0] < ttl:
            self._cache.move_to_end(key)
            return entry[1]
        if key not in self._pending:
            if len(self._pending) >= 16:
                raise YahooError("Yahoo Finance 조회 요청을 처리 중입니다. 잠시 후 다시 시도하세요.", 503)

            async def fetch():
                try:
                    rows = await load()
                    self._cache[key] = time.monotonic(), rows
                    self._cache.move_to_end(key)
                    while len(self._cache) > 32 or sum(len(e[1]) for e in self._cache.values()) > 150000:
                        self._cache.popitem(last=False)
                    return rows
                finally:
                    self._pending.pop(key, None)

            self._pending[key] = asyncio.create_task(fetch())
        return await asyncio.shield(self._pending[key])

    async def _recent_days(self, ticker) -> list[dict]:
        now = int(time.time())
        return await self._cached((ticker, "recent-daily"), REFRESH,
                                  lambda: self._read(ticker, "1d", now - 110 * DAY, now))

    async def _daily_history(self, ticker, *, refresh_latest=False) -> list[dict]:
        key = ticker, "daily-history"
        rows = await self._cached(key, DAY, lambda: self._read(ticker, "1d", None, None))
        if not rows:
            raise YahooError("Yahoo Finance에서 이 종목의 일봉을 제공하지 않습니다.")
        if refresh_latest and key in self._cache and time.monotonic() - self._cache[key][0] >= REFRESH:
            rows = dedupe_sort(rows + await self._recent_days(ticker))
        return rows

    async def period_bars(self, ticker, tf, count, *, before=None) -> list[dict]:
        if tf not in PERIOD_TFS:
            raise ValueError(tf)
        rows = await self._daily_history(ticker, refresh_latest=before is None)
        bars = rows if tf == "D" else aggregate(rows, tf)
        stop = len(bars) if before is None else bisect_left(bars, before, key=itemgetter("time"))
        return bars[max(0, stop - count):stop]

    async def period_prefix(self, ticker: str, tf: str, from_date: int, before: int) -> tuple[list[dict], int, bool]:
        """Include the target's surrounding history and all bars up to the loaded chart."""
        if tf not in PERIOD_TFS:
            raise ValueError(tf)
        rows = await self._daily_history(ticker)
        bars = rows if tf == "D" else aggregate(rows, tf)
        stop = bisect_left(bars, before, key=itemgetter("time"))
        target = bisect_left(bars, bucket_start(from_date, tf), hi=stop, key=itemgetter("time"))
        first = max(0, target - 90)
        return bars[first:stop], rows[0]["time"], first > 0

    async def _minute_page(self, ticker, days, tf, before) -> list[dict]:
        now = int(time.time())
        earliest = now - _MINUTE_RETENTION[tf] * DAY + 60
        # before는 현지 벽시계이고 요청 시각은 UTC입니다. 최대 시차만큼 겹쳐 받은 뒤 현지 시각으로 자릅니다.
        end = min(now, before + 14 * 3600) if before is not None else now
        base = "1" if tf == "3" else tf
        span = 7 if base == "1" else max(7, 2 * days + 4)
        bars = []
        while end > earliest:
            start = max(earliest, end - span * DAY)
            page = await self._read(ticker, base + "m", start, end)
            if before is not None:
                page = [b for b in page if b["time"] < before]
            bars = dedupe_sort(page + bars)
            dates = sorted({b["time"] // DAY for b in bars})
            if len(dates) > days:
                bars = [b for b in bars if b["time"] // DAY >= dates[-days]]
                break
            if not page and start < end - 6 * DAY:
                # 한 주 내내 봉이 없으면 더 오래된 분봉 제공 범위가 끝난 것으로 처리합니다.
                break
            end = start
        if tf == "3" and bars:
            grouped = aggregate(bars, tf)
            # 맨 앞 구간을 도중부터 받은 경우 전체 OHLC를 알 수 없습니다.
            if grouped[0]["time"] < bars[0]["time"]:
                grouped = grouped[1:]
            return grouped
        return bars

    async def minute_bars(self, ticker, days, tf="1", *, before=None) -> list[dict]:
        if tf not in MINUTE_TFS:
            raise ValueError(tf)
        ttl = REFRESH if before is None else 3600
        return await self._cached((ticker, "minute", tf, days, before), ttl,
                                  lambda: self._minute_page(ticker, days, tf, before))

    async def recent_bars(self, ticker, tf, *, since=None) -> list[dict]:
        if tf in PERIOD_TFS:
            rows = await self._recent_days(ticker)
            if tf != "D":
                rows = aggregate(rows, tf)
                # Recovery can reach the first week/month of this UTC request, whose OHLC
                # may be partial. A local midnight beyond the latest timezone offset is safe.
                if since is not None and rows and rows[0]["time"] < time.time() - 110 * DAY + 14 * 3600:
                    rows = rows[1:]
        else:
            if tf not in MINUTE_TFS:
                raise ValueError(tf)
            base = "1" if tf == "3" else tf
            now = int(time.time())
            rows = await self._cached((ticker, "recent-minute", base), REFRESH,
                                      lambda: self._read(ticker, base + "m", now - 4 * DAY, now))
            if tf == "3" and rows:
                first = rows[0]["time"]
                rows = aggregate(rows, tf)
                if rows[0]["time"] < first:
                    rows = rows[1:]
        if since is None:
            return rows[-3:]
        # Keep one prior candle so the caller can distinguish a missing anchor from an older one.
        start = bisect_left(rows, since, key=itemgetter("time"))
        return rows[max(0, start - 1):]

    async def close(self):
        self._closed = True
        await self._status.close()
        if self._pending:
            await asyncio.gather(*list(self._pending.values()), return_exceptions=True)
        self._cache.clear()

    async def quote_status(self, ticker: str) -> dict:
        return await self._status.get(ticker)
