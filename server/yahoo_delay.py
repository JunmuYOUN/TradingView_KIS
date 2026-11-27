"""Yahoo가 공개한 종목별 시세 지연, 세션 및 마지막 시세 원본 시각."""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import OrderedDict
from urllib.parse import quote

import httpx

from .codes import valid_yahoo_feed_ticker
from .yahoo_symbols import YAHOO_SYMBOLS


# Yahoo Finance의 거래소별 보조 기준. 종목 응답의 값이 있으면 항상 그 값을 사용합니다.
# https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html
_VENUE_DELAY = {"CME": 10, "CBT": 10, "CBOT": 10, "NYM": 30, "NYMEX": 30, "CMX": 30,
                "COMEX": 30, "NYB": 30, "ICE": 30, "CXI": 15, "CBOE": 15}
_QUOTE_SCRIPT = re.compile(
    r'<script\b[^>]*\bdata-url="https://query[12]\.finance\.yahoo\.com/v7/finance/quote\?[^"]*"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_PHASE = {"REGULAR": "regular", "PRE": "pre", "POST": "post", "CLOSED": "closed"}


def _number(value):
    if isinstance(value, dict):
        value = value.get("raw")
    if type(value) in (int, float) and math.isfinite(value) and value == int(value):
        return int(value)
    return None


def _same_symbol(ticker, result):
    aliases = {"JPY=X": "USDJPY=X"}
    return result == ticker or result == aliases.get(ticker, ticker)


def _public_quote(page, ticker):
    # Yahoo의 공개 종목 페이지에 내장된 quoteResponse만 사용합니다. 관련 종목은 제외합니다.
    for match in _QUOTE_SCRIPT.finditer(page):
        try:
            body = json.loads(match.group(1))["body"]
            result = json.loads(body)["quoteResponse"]["result"]
        except (KeyError, TypeError, ValueError):
            continue
        for item in result or []:
            if isinstance(item, dict) and _same_symbol(ticker, item.get("symbol")):
                return item
    return {}


def _session(meta, now):
    periods = meta.get("currentTradingPeriod") or {}
    valid = []
    for phase in ("regular", "pre", "post"):
        interval = periods.get(phase) or {}
        start, end = _number(interval.get("start")), _number(interval.get("end"))
        if start and end and start < end:
            if start <= now < end:
                return phase, start
            valid.append((start, end))
    # Yahoo 도착 시각에 인접한 거래일의 세션 범위만 개장 판단에 씁니다.
    near = any(start - 4 * 86400 <= now <= end + 4 * 86400 for start, end in valid)
    return ("closed" if near else "unknown"), None


def status_from_metadata(ticker, instrument_quote, chart_meta, *, now, use_quote_state=True):
    quote_data, meta = instrument_quote, chart_meta
    minutes = _number(quote_data.get("exchangeDataDelayedBy"))
    label = str(quote_data.get("quoteSourceName") or "").lower()
    if minutes is not None and not 0 <= minutes <= 1440:
        minutes = None
    if minutes == 0 and "delay" in label:
        minutes = None  # 금액·분봉의 경과 시간으로 공급자가 밝히지 않은 지연 분수를 추정하지 않습니다.
    if minutes is not None:
        feed, source = ("delayed" if minutes > 0 else "realtime"), "quote"
    elif "delay" in label:
        feed, source = "delayed", "quote"
    elif "real time" in label or "real-time" in label or "realtime" in label:
        minutes, feed, source = 0, "realtime", "quote"
    else:
        known = YAHOO_SYMBOLS.get("YF:" + ticker)
        exchange = quote_data.get("exchange") or meta.get("exchangeName") or (known.market if known else "")
        minutes = _VENUE_DELAY.get(str(exchange).upper())
        feed = "delayed" if minutes is not None else "unknown"
        source = "exchange" if minutes is not None else None

    phase, session_start = _session(meta, now)
    if use_quote_state:
        phase = _PHASE.get(quote_data.get("marketState"), phase)
    market_open = None if phase == "unknown" else phase != "closed"
    stamps = [meta.get("regularMarketTime"), quote_data.get("regularMarketTime")]
    if phase in ("pre", "post"):
        stamps.append(quote_data.get(phase + "MarketTime"))
    as_of = max((t for raw in stamps if (t := _number(raw)) and 0 < t <= now + 60), default=None)
    stale = None
    if as_of is not None and market_open and minutes is not None:
        # 개장 직후에는 공시 지연 시간과 5분의 갱신 여유가 지나야 이전 종가를 오래된 시세로 분류합니다.
        since = max(as_of, session_start or as_of)
        stale = now - since > (minutes + 5) * 60
    return {"delay_minutes": minutes, "feed": feed, "delay_source": source, "as_of": as_of,
            "market_open": market_open, "market_state": phase, "stale": stale}


class YahooQuoteStatus:
    def __init__(self, *, allow_stocks: bool = False):
        self._allow_stocks = allow_stocks
        self._client = None
        self._quotes = OrderedDict()
        self._statuses = OrderedDict()
        self._pending = {}
        self._closed = False
        self._limit = asyncio.Semaphore(3)

    async def _get(self, url, size):
        if self._client is None:
            self._client = httpx.AsyncClient(headers={"User-Agent": "KIS-Chart/1.0"}, timeout=3.0,
                                             follow_redirects=False)
        async with self._client.stream("GET", url) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > size:
                    raise ValueError("response size")
                content.extend(chunk)
            return content

    async def _quote(self, ticker):
        code = quote(ticker, safe="")
        page = await self._get(f"https://finance.yahoo.com/quote/{code}/", 3_000_000)
        return _public_quote(page.decode("utf-8"), ticker)

    async def _chart(self, ticker):
        code = quote(ticker, safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}?range=1d&interval=1d"
        data = json.loads(await self._get(url, 120_000))
        result = data["chart"]["result"][0]["meta"]
        return result if _same_symbol(ticker, result.get("symbol")) else {}

    @staticmethod
    def _save(cache, ticker, value):
        cache[ticker] = time.monotonic(), value
        cache.move_to_end(ticker)
        if len(cache) > 128:
            cache.popitem(last=False)

    async def _optional(self, read, ticker):
        try:
            return await asyncio.wait_for(read(ticker), timeout=3.5)
        except (TimeoutError, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return {}

    async def _refresh(self, ticker):
        try:
            async with self._limit:
                entry = self._quotes.get(ticker)
                if entry and time.monotonic() - entry[0] < (900 if entry[1] else 120):
                    info = entry[1]
                    meta = await self._optional(self._chart, ticker)
                    fresh_quote = False
                else:
                    info, meta = await asyncio.gather(self._optional(self._quote, ticker),
                                                      self._optional(self._chart, ticker))
                    self._save(self._quotes, ticker, info)
                    fresh_quote = True
                result = status_from_metadata(ticker, info, meta, now=int(time.time()), use_quote_state=fresh_quote)
                self._save(self._statuses, ticker, result)
                return result
        finally:
            self._pending.pop(ticker, None)

    async def get(self, ticker):
        snapshot = self._statuses.get(ticker)
        if snapshot and time.monotonic() - snapshot[0] < 60:
            return snapshot[1]
        if ticker not in self._pending:
            if (self._closed or len(self._pending) >= 3
                    or not valid_yahoo_feed_ticker(ticker, allow_stocks=self._allow_stocks)):
                return status_from_metadata(ticker, {}, {}, now=int(time.time()))
            self._pending[ticker] = asyncio.create_task(self._refresh(ticker))
        return await asyncio.shield(self._pending[ticker])

    async def close(self):
        self._closed = True
        if self._pending:
            await asyncio.gather(*list(self._pending.values()), return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
