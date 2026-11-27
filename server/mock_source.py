"""KIS 키 없이 화면을 확인하기 위한 가짜 시세.

- 24시간 내내 1분봉과 실시간 틱이 생성됩니다 (실제 장 시간과 무관).
- 종목코드마다 시드가 고정되어 같은 코드는 비슷한 모양의 차트가 나옵니다.
"""
from __future__ import annotations

import asyncio
import math
import random
import zlib

from .bars import MINUTE_TFS, aggregate, bucket_start, kst_epoch, now_epoch, tick_size
from .codes import index_kis_code, is_index, is_overseas
from .hub import TickHub

DAY = 86400
HISTORY_START = kst_epoch("19900101")
# 데모 전용 가상 기준값. 실제 지수 시세와 독립적으로 재현되는 차트를 만듭니다.
DEMO_INDEX_LEVELS = {"0001": 3000, "1001": 800, "2001": 400, "3003": 1400,
                     ".DJI": 40000, "NDX": 20000, "SPX": 5000, "COMP": 16000, "VIX": 20}


def _round_tick(p: float) -> float:
    ts = tick_size(p)
    return float(max(ts, round(p / ts) * ts))


def _round_cent(p: float) -> float:
    return max(0.01, round(p, 2))


class _MockSymbol:
    def __init__(self, code: str) -> None:
        seed = zlib.crc32(code.encode())
        self.rng = random.Random(seed)
        self.fractional_ticks = is_index(code) or is_overseas(code)
        self.round = _round_cent if self.fractional_ticks else _round_tick
        if is_index(code):
            base = DEMO_INDEX_LEVELS.get(index_kis_code(code), 1000 + seed % 2000)
        elif is_overseas(code):
            base = 20 + (seed % 400) + (seed % 100) / 100  # 20 ~ 420 달러
        else:
            base = 5000 + (seed % 200) * 1000          # 5,000 ~ 204,000 원
        self.base = base
        self.seed = seed
        self.phase = self.rng.uniform(0, math.tau)
        self.live_minutes: dict[int, dict] = {}
        self.live_days: dict[int, dict] = {}
        minute = bucket_start(now_epoch(), "1")
        day = bucket_start(minute, "D")
        today = self.minute_range(day, minute)
        self.live_minutes[minute] = today[-1]
        self.live_days[day] = aggregate(today, "D")[0]
        self.price = today[-1]["close"]

    def daily_close(self, day: int) -> float:
        # 날짜에 고정된 완만한 파동과 일별 변동. 조회 구간/순서가 가격에 영향을 주지 않습니다.
        i = day // DAY
        p = self.phase
        noise = random.Random(self.seed + i).uniform(-0.014, 0.014)
        return self.round(self.base * math.exp(0.24 * math.sin(i / 117 + p)
                          + 0.10 * math.sin(i / 23 + p) + 0.04 * math.sin(i / 6.3 + p) + noise))

    def daily_bar(self, day: int) -> dict:
        if day in self.live_days:
            return dict(self.live_days[day])
        weekday = (day // DAY + 3) % 7
        prev = day - (3 if weekday == 0 else 1) * DAY
        opening, close = self.daily_close(prev), self.daily_close(day)
        rng = random.Random((self.seed << 32) + day)
        return {"time": day, "open": opening, "high": self.round(max(opening, close) * rng.uniform(1.002, 1.023)),
                "low": self.round(min(opening, close) * rng.uniform(0.977, 0.998)), "close": close,
                "volume": float(rng.randint(200_000, 5_000_000))}

    def minute_range(self, first: int, last: int) -> list[dict]:
        bars = []
        for day in range(bucket_start(first, "D"), last + 1, DAY):
            # 당일 틱이 반영된 일봉이 과거 분봉 생성에 섞이지 않도록 원래의 날짜별 기준을 사용합니다.
            weekday = (day // DAY + 3) % 7
            prev = day - (3 if weekday == 0 else 1) * DAY
            opening, close = self.daily_close(prev), self.daily_close(day)
            rng = random.Random((self.seed << 32) + day)
            phase = rng.uniform(0, math.tau)

            def price_at(minute: int) -> float:
                f = minute / 1440
                noise = zlib.crc32((day + minute * 60).to_bytes(8, "little"), self.seed) / 0xffffffff - 0.5
                wave = (0.012 * math.sin(minute / 96 + phase) + 0.004 * math.sin(minute / 22 + phase)
                        + 0.0018 * math.sin(minute / 4.7 + phase) + 0.003 * noise)
                return self.round((opening + (close - opening) * f) * (1 + math.sin(math.pi * f) * wave))

            start_minute = max(0, (first - day) // 60)
            opening_price = price_at(start_minute)
            for i in range(start_minute, min(1439, (last - day) // 60) + 1):
                t = day + i * 60
                closing_price = price_at(i + 1)
                if t in self.live_minutes:
                    bars.append(dict(self.live_minutes[t]))
                else:
                    # 인접 요청의 겹치는 분봉도 동일하도록 난수는 절대 시각에 고정합니다.
                    bits = zlib.crc32(t.to_bytes(8, "little"), self.seed)
                    high = self.round(max(opening_price, closing_price) * (1 + (bits & 255) / 250_000))
                    low = self.round(min(opening_price, closing_price) * (1 - ((bits >> 8) & 255) / 250_000))
                    bars.append({"time": t, "open": opening_price, "high": high, "low": low,
                                 "close": closing_price, "volume": float(500 + (bits >> 16) % 19_500)})
                opening_price = closing_price
        return bars

    def apply_tick(self, t: int, p: float, v: float) -> None:
        self.price = p
        for bars, tf in ((self.live_minutes, "1"), (self.live_days, "D")):
            s = bucket_start(t, tf)
            last = bars.get(s)
            if last is not None:
                last["high"] = max(last["high"], p)
                last["low"] = min(last["low"], p)
                last["close"] = p
                last["volume"] += v
            else:
                bars[s] = {"time": s, "open": p, "high": p, "low": p, "close": p, "volume": v}

    def next_tick(self) -> tuple[int, float, float]:
        ts = 0.01 * self.rng.randint(1, 5) if self.fractional_ticks else tick_size(self.price)
        r = self.rng.random()
        step = 0 if r < 0.85 else (1 if r < 0.925 else -1)
        p = self.round(self.price + step * ts)
        v = float(self.rng.randint(1, 300))
        return now_epoch(), p, v


class MockHub(TickHub):
    def __init__(self, source: "MockSource") -> None:
        super().__init__()
        self.source = source
        self._tasks: dict[str, asyncio.Task] = {}

    async def on_first_subscribe(self, code: str) -> None:
        self.publish(code, {"type": "status", "state": "live", "msg": "MOCK 실시간 틱"})
        self._tasks[code] = asyncio.create_task(self._run(code))

    async def on_last_unsubscribe(self, code: str) -> None:
        t = self._tasks.pop(code, None)
        if t:
            t.cancel()

    async def _run(self, code: str) -> None:
        sym = self.source.symbol(code)
        try:
            while True:
                await asyncio.sleep(sym.rng.uniform(0.2, 0.8))
                t, p, v = sym.next_tick()
                sym.apply_tick(t, p, v)
                self.publish(code, {"type": "tick", "t": t, "p": p, "v": v})
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        for t in self._tasks.values():
            t.cancel()


class MockSource:
    name = "mock"
    history_start = HISTORY_START

    def __init__(self) -> None:
        self._symbols: dict[str, _MockSymbol] = {}
        self.hub = MockHub(self)

    def symbol(self, code: str) -> _MockSymbol:
        if code not in self._symbols:
            self._symbols[code] = _MockSymbol(code)
        return self._symbols[code]

    async def minute_bars(self, code: str, days: int, tf: str = "1", before: int | None = None) -> list[dict]:
        now = bucket_start(now_epoch(), "1")
        width = MINUTE_TFS[tf] * 60
        last_bucket = bucket_start(min(now, before - 1) if before is not None else now, tf)
        first = max(HISTORY_START, last_bucket + width - days * DAY)
        last = min(now, last_bucket + width - 60)
        if last < first:
            return []
        sym = self.symbol(code)
        bars = sym.minute_range(first, last)
        return bars if tf == "1" else aggregate(bars, tf)

    async def period_bars(self, code: str, tf: str, count: int, before: int | None = None) -> list[dict]:
        today = bucket_start(now_epoch(), "D")
        day = min(today, bucket_start(before - 1, "D")) if before is not None else today
        if day < HISTORY_START:
            return []
        # 커서가 주중/월중이라도 봉 시작 < before 인 마지막 봉은 전체 OHLCV를 반환합니다.
        end_bucket = bucket_start(day, tf)
        while day < today and bucket_start(day + DAY, tf) == end_bucket:
            day += DAY
        sym = self.symbol(code)
        daily = []
        previous = None
        groups = 0
        while day >= HISTORY_START:
            if (day // DAY + 3) % 7 < 5 or day in sym.live_days:
                bucket = bucket_start(day, tf)
                if bucket != previous:
                    if groups >= count:
                        break
                    groups += 1
                    previous = bucket
                daily.append(sym.daily_bar(day))
            day -= DAY
        daily.reverse()
        return daily if tf == "D" else aggregate(daily, tf)

    async def close(self) -> None:
        await self.hub.close()
