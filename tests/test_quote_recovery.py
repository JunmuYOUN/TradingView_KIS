import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException, Request

from server import main
from server.bars import kst_epoch
from server.kis_source import KisSource
from server.yahoo_chart import load_chart
from server.yahoo_source import YahooSource


def candle(stamp, close):
    return {"time": stamp, "open": close, "high": close, "low": close, "close": close, "volume": 10}


class QuoteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_yahoo_normal_poll_and_full_intraday_gap(self):
        start = kst_epoch("20261125", "100000")
        history = [candle(start + i * 60, 100 + i) for i in range(11)]
        calls = []

        async def load(ticker, interval, left, right):
            calls.append(interval)
            return [b for b in history if left <= b["time"] < right]

        source = YahooSource(load)
        self.addAsyncCleanup(source.close)
        with patch("server.yahoo_source.time.time", return_value=start + 11 * 60):
            rows, recovery, cursor = await main.quote_update(source, "^GSPC", "1", history[-2]["time"], None)
            self.assertEqual((rows, recovery, cursor), (history[-2:], None, None))
            self.assertEqual(len(calls), 1)
            rows, recovery, cursor = await main.quote_update(source, "^GSPC", "1", start, None)
            self.assertEqual((rows, recovery, cursor), (history, "backfilled", None))
            self.assertEqual(len(calls), 1)

    async def test_latest_http_contract_and_page_validation(self):
        stamp = kst_epoch("20261125", "100000")
        tail = [candle(stamp, 20), candle(stamp + 60, 21)]

        class Quotes:
            name = "kis"

            async def recent_bars(self, ticker, tf):
                return tail

        with patch.object(main, "chart_source", return_value=(Quotes(), "005930")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                query = {"code": "005930", "tf": "1", "since": stamp}
                result = (await client.get("/api/quotes/latest", params=query)).json()
                self.assertEqual((result["bars"], result["recovery"], result["next_before"]), (tail, None, None))
                bad = await client.get("/api/quotes/latest", params={**query, "before": stamp})
                self.assertEqual(bad.status_code, 400)

    async def test_paged_recovery_uses_provider_bars_and_reports_unavailable_history(self):
        start = kst_epoch("20261125", "100000")
        # A session break is a gap in clock time but does not require a fabricated candle.
        history = [candle(start + minutes * 60, minutes) for minutes in (0, 1, 2, 100, 101, 102, 103, 104)]

        class PagedKis:
            name = "kis"

            async def recent_bars(self, code, tf):
                return history[-3:]

            async def minute_bars(self, code, days, tf, *, before):
                return [bar for bar in history if bar["time"] < before][-2:]

        for anchor, expected_status in [(start, "backfilled"), (start - 60, "unavailable")]:
            source = PagedKis()
            cursor = None
            windows = []
            while True:
                rows, status, next_before = await main.quote_update(source, "NAS:AAPL", "1", anchor, cursor)
                windows[:0] = rows
                if next_before is None:
                    break
                self.assertTrue(cursor is None or next_before < cursor)
                cursor = next_before
            self.assertEqual(windows, history)
            self.assertEqual(status, expected_status)

    async def test_disconnected_chart_cancels_kis_pagination(self):
        waiting = asyncio.Event()
        cancelled = asyncio.Event()
        calls = []

        class PagedKis(KisSource):
            def __init__(self):
                pass

            async def _period_page(self, code, tf, end):
                calls.append(end)
                if len(calls) == 2:
                    waiting.set()
                    try:
                        await asyncio.Future()
                    finally:
                        cancelled.set()
                return [candle(kst_epoch(end), 100)]

        receive = asyncio.Queue()
        request = Request({"type": "http"}, receive=receive.get)
        await receive.put({"type": "http.request", "body": b"", "more_body": False})
        with patch.object(main, "chart_source", return_value=(PagedKis(), "005930")):
            query = asyncio.create_task(main.api_candles(request, "005930", "D", None, 20, None, None))
            await asyncio.wait_for(waiting.wait(), timeout=2)
            await receive.put({"type": "http.disconnect"})
            with self.assertRaises(HTTPException) as stopped:
                await asyncio.wait_for(query, timeout=2)
        self.assertEqual(stopped.exception.status_code, 499)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(calls), 2)

    async def test_kis_index_snapshot_does_not_claim_disconnected_cached_history(self):
        old = kst_epoch("20261125", "093000")
        new = kst_epoch("20261125", "120500")
        source = object.__new__(KisSource)
        source.s = SimpleNamespace(env="real")
        source._us_index_minutes = {"INDEX:VIX": (0, {old: candle(old, 19)})}

        class IndexRest:
            async def get(self, path, tr_id, params):
                return {"output2": [dict(stck_bsop_date="20261125", stck_cntg_hour="120500",
                                         optn_oprc=20, optn_hgpr=20, optn_lwpr=20, optn_prpr=20, cntg_vol=10)]}

        source.rest = IndexRest()
        rows, recovery, cursor = await main.quote_update(source, "INDEX:VIX", "1", old, None)
        self.assertEqual((rows, recovery, cursor), ([candle(new, 20)], "unavailable", None))

    async def test_mobile_live_quote_updates_candle_without_duplicating_volume(self):
        start = kst_epoch("20261125", "100000")
        result = {
            "meta": {"exchangeTimezoneName": "UTC"},
            "timestamp": [start, start + 300, start + 480],
            "indicators": {"quote": [{"open": [99, 100, 103], "high": [101, 105, 110],
                                      "low": [98, 99, 97], "close": [100, 102, 108],
                                      "volume": [20, 50, 57]}]},
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"chart": {"result": [result]}}))
        client = httpx.AsyncClient(transport=transport)
        with patch("server.yahoo_chart.httpx.AsyncClient", return_value=client):
            rows = await load_chart("^GSPC", "5m", start, start + 500)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1], dict(time=start + 300, open=100, high=110, low=97, close=108, volume=57))
