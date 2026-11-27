"""정확히 입력된 Yahoo Finance 티커의 상품 유형과 표시 정보를 확인합니다."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

import httpx

from .codes import is_yahoo, yahoo_ticker
from .yahoo_symbols import YAHOO_SYMBOLS, YahooInstrument

MAX_ENTRIES = 128
VALID_TTL = 86400
MISSING_TTL = 300
MAX_BYTES = 65536
_KINDS = {"FUTURE": "선물", "CURRENCY": "환율", "INDEX": "지수"}


class YahooLookupUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Yahoo Finance에서 종목을 확인할 수 없습니다. 잠시 후 다시 시도하세요.")


def _label(value, limit=60):
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in value).split())[:limit]


def _instrument(code, meta):
    ticker = yahoo_ticker(code)
    asset = "FUTURE" if ticker.endswith("=F") else "CURRENCY" if ticker.endswith("=X") else "INDEX"
    symbol, actual = meta["symbol"], meta["instrumentType"]
    if not isinstance(symbol, str) or not isinstance(actual, str):
        raise ValueError("missing symbol metadata")
    matches = symbol == ticker
    if asset == "CURRENCY":
        # Yahoo uses both JPY=X and USDJPY=X for the same dollar-based pair.
        matches = (symbol if len(symbol) != 5 else "USD" + symbol) == (ticker if len(ticker) != 5 else "USD" + ticker)
    if not matches or actual != asset:
        return None
    name = _label(meta.get("longName") or meta.get("shortName")) or ticker
    market = _label(meta.get("fullExchangeName") or meta.get("exchangeName"), 32) or "Yahoo Finance"
    hint = meta.get("priceHint")
    precision = min(8, max(0, hint)) if type(hint) is int else 2
    aliases = {"FUTURE": ("futures", "해외선물", ticker[:-2], ticker[:-2] + "1!"),
               "CURRENCY": ("FX", "외환", "forex", "currency", ticker[:-2]),
               "INDEX": ("index", ticker[1:])}[asset]
    if asset == "CURRENCY":
        precision = max(precision, 5)
    return YahooInstrument(ticker, name, "FX" if asset == "CURRENCY" else market,
                           _KINDS[asset], name, precision, aliases)


class YahooLookup:
    def __init__(self, path: Path, transport=None):
        self.path = path
        self.transport = transport
        self.cache: OrderedDict[str, tuple[float, YahooInstrument | None]] = OrderedDict()
        self.pending: dict[str, asyncio.Task] = {}
        self.retry_at = 0.0
        self._load_cache()

    def _store(self, code, expires, item):
        self.cache[code] = (expires, item)
        self.cache.move_to_end(code)
        if len(self.cache) > MAX_ENTRIES:
            self.cache.popitem(last=False)

    def items(self):
        now = time.time()
        return [(code, item) for code, (expires, item) in self.cache.items() if item and expires > now]

    def get(self, code):
        entry = self.cache.get(code)
        return entry[1] if entry and entry[0] > time.time() else None

    def _load_cache(self):
        try:
            with self.path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                return
            rows = json.loads(data)
            if not isinstance(rows, list):
                return
            now = time.time()
            for row in rows[-MAX_ENTRIES:]:
                if not isinstance(row, list) or len(row) != 3:
                    continue
                code, expires, meta = row
                if (not is_yahoo(code) or code in YAHOO_SYMBOLS or not isinstance(meta, dict)
                        or "symbol" not in meta or "instrumentType" not in meta
                        or type(expires) not in (int, float) or not now < expires <= now + VALID_TTL):
                    continue
                if item := _instrument(code, meta):
                    self._store(code, expires, item)
        except (OSError, ValueError, TypeError):
            return

    def _save_cache(self):
        rows = []
        types = {kind: asset for asset, kind in _KINDS.items()}
        for code, item in self.items():
            meta = {"symbol": item.ticker, "instrumentType": types[item.kind], "shortName": item.name,
                    "fullExchangeName": item.market, "priceHint": item.precision}
            rows.append([code, self.cache[code][0], meta])
        tmp = self.path.with_name(self.path.name + "." + secrets.token_hex(4) + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            logging.getLogger("symbols").warning("Yahoo 종목 목록 캐시를 저장하지 못했습니다")
        finally:
            tmp.unlink(missing_ok=True)

    async def _fetch(self, code):
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(yahoo_ticker(code), safe="")
        try:
            async with httpx.AsyncClient(timeout=4, follow_redirects=False, transport=self.transport,
                                         headers={"User-Agent": "KIS-Chart/1.0"}) as client:
                async with client.stream("GET", url, params={"range": "1d", "interval": "1d"}) as response:
                    if response.status_code == 429:
                        self.retry_at = time.monotonic() + 30
                    if response.status_code not in (200, 404, 422):
                        raise YahooLookupUnavailable()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise YahooLookupUnavailable()
            chart = json.loads(data)["chart"]
            error = chart.get("error")
            if error and str(error.get("code", "")).casefold() == "not found":
                return None
            if error or response.status_code != 200:
                raise YahooLookupUnavailable()
            return _instrument(code, chart["result"][0]["meta"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise YahooLookupUnavailable() from None

    async def resolve(self, code):
        entry = self.cache.get(code)
        if entry and entry[0] > time.time():
            self.cache.move_to_end(code)
            return entry[1]
        if code not in self.pending:
            if len(self.pending) >= 4 or self.retry_at > time.monotonic():
                raise YahooLookupUnavailable()

            async def fetch():
                try:
                    item = await asyncio.wait_for(self._fetch(code), 5)
                    self._store(code, time.time() + (VALID_TTL if item else MISSING_TTL), item)
                    if item:
                        self._save_cache()
                    return item
                except asyncio.TimeoutError:
                    raise YahooLookupUnavailable() from None
                finally:
                    self.pending.pop(code, None)

            self.pending[code] = asyncio.create_task(fetch())
        return await asyncio.shield(self.pending[code])

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
