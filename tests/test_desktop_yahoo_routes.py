import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from server import main
from server.symbols import BUILTIN, SymbolBook


class DesktopYahooRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        book = SymbolBook(Path(directory.name) / "symbols.json", download=False)
        book._set([*BUILTIN, ("086520", "에코프로", "KOSDAQ", "주식")])
        self.bar = {"time": 1704067200, "open": 3, "high": 4, "low": 2, "close": 3, "volume": 5}
        self.yahoo = SimpleNamespace(
            name="yahoo", period_bars=AsyncMock(return_value=[self.bar]),
            minute_bars=AsyncMock(return_value=[self.bar]), recent_bars=AsyncMock(return_value=[self.bar]),
            quote_status=AsyncMock(return_value={"delay_minutes": 15}),
            period_prefix=AsyncMock(return_value=([self.bar], self.bar["time"], False)),
        )
        settings = SimpleNamespace(source="yahoo", env="demo", market="J")
        state = SimpleNamespace(symbols=book, yahoo=self.yahoo, source=self.yahoo,
                                settings=settings, desktop=True)
        app_state = patch.object(main.app, "state", state)
        app_state.start()
        self.addCleanup(app_state.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_original_ids_map_to_verified_yahoo_tickers(self):
        cases = {"005930": "005930.KS", "086520": "086520.KQ", "NAS:AAPL": "AAPL",
                 "NYS:BRK.B": "BRK-B", "INDEX:0001": "^KS11", "INDEX:1001": "^KQ11",
                 "INDEX:2001": "^KS200", "INDEX:.DJI": "^DJI", "INDEX:COMP": "^IXIC",
                 "INDEX:NDX": "^NDX", "INDEX:SPX": "^GSPC", "INDEX:VIX": "^VIX", "YF:KRW=X": "KRW=X"}
        for code, expected in cases.items():
            with self.subTest(code=code):
                response = await self.client.get("/api/candles", params={"code": code, "tf": "D", "count": 10})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual((data["code"], data["source"], data["bars"]), (code, "yahoo", [self.bar]))
                self.yahoo.period_bars.assert_awaited_with(expected, "D", 10, before=None)
                self.yahoo.quote_status.assert_awaited_with(expected)

    async def test_all_chart_operations_use_mapped_ticker(self):
        config = (await self.client.get("/api/config")).json()
        self.assertEqual((config["source"], config["quote_transport"]), ("yahoo", "https"))
        stamp = self.bar["time"]
        for path, params in [("/api/candles", {"tf": "5", "days": 3}),
                             ("/api/candles", {"tf": "D", "before": stamp + 86400, "from_date": stamp}),
                             ("/api/quotes/latest", {"tf": "5", "since": stamp})]:
            result = await self.client.get(path, params={**params, "code": "086520"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["source"], "yahoo")
        self.yahoo.minute_bars.assert_awaited_once_with("086520.KQ", 3, "5", before=None)
        self.yahoo.period_prefix.assert_awaited_once_with("086520.KQ", "D", stamp, stamp + 86400)
        self.yahoo.recent_bars.assert_awaited_once_with("086520.KQ", "5", since=stamp)

    async def test_unknown_venue_and_unmapped_index_fail_without_quotes(self):
        for code in ("000000", "INDEX:3003"):
            response = await self.client.get("/api/candles", params={"code": code})
            self.assertEqual(response.status_code, 404)
            self.assertTrue(response.json()["detail"])
        self.yahoo.period_bars.assert_not_awaited()

    async def test_other_modes_preserve_their_instrument_ids(self):
        for name in ("kis", "mock"):
            main.app.state.source = SimpleNamespace(name=name)
            main.app.state.settings.source = name
            self.assertEqual((await main.chart_instrument("005930"))[1], "005930")
            self.assertEqual((await main.chart_instrument("NYS:BRK.B"))[1], "NYS:BRK.B")
            self.assertEqual(await main.chart_instrument("YF:^VIX"), (self.yahoo, "^VIX"))
