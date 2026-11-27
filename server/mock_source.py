"""KIS 키 없이 화면을 확인하기 위한 가짜 시세.

- 24시간 내내 1분봉과 실시간 틱이 생성됩니다 (실제 장 시간과 무관).
- 종목코드마다 시드가 고정되어 같은 코드는 비슷한 모양의 차트가 나옵니다.
"""
from __future__ import annotations

import asyncio
import math
import random
import zlib

from .bars import aggregate, bucket_start, now_epoch, tick_size
from .codes import is_overseas
from .hub import TickHub

MAX_MINUTE_DAYS = 30
DAILY_COUNT = 800


def _round_tick(p: float) -> float:
    ts = tick_size(p)
    return float(max(ts, round(p / ts) * ts))


def _round_cent(p: float) -> float:
    return max(0.01, round(p, 2))


def _walk_backward(rng: random.Random, last_close: float, n: int, vol: float, cycle: float,
                   trend: float, rounder=_round_tick) -> list[tuple]:
    """마지막 종가에서 시작해 과거 방향으로 n 개의 (open, high, low, close) 를 만듭니다."""
    closes = [last_close]
    drift_phase = rng.random() * 6.28
    for i in range(n - 1):
        # 몇 개의 주기 성분을 섞어 파동 모양이 보이도록 합니다
        drift = trend * vol * (math.sin(i / cycle + drift_phase) + 0.5 * math.sin(i / (cycle * 0.27)))
        r = rng.gauss(drift, vol)
        closes.append(closes[-1] / math.exp(r))
    closes.reverse()
    out = []
    prev = closes[0] * math.exp(rng.gauss(0, vol))
    for c in closes:
        o = prev
        hi = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.6)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.6)))
        o, hi, lo, cr = (rounder(x) for x in (o, hi, lo, c))
        hi = max(hi, o, cr)
        lo = min(lo, o, cr)
        out.append((o, hi, lo, cr))
        prev = c
    return out


class _MockSymbol:
    def __init__(self, code: str) -> None:
        seed = zlib.crc32(code.encode())
        self.rng = random.Random(seed)
        self.overseas = is_overseas(code)
        if self.overseas:
            self.round = _round_cent
            base = 20 + (seed % 400) + (seed % 100) / 100  # 20 ~ 420 달러
        else:
            self.round = _round_tick
            base = 5000 + (seed % 200) * 1000          # 5,000 ~ 204,000 원
        self.price = self.round(float(base))
        now = now_epoch()
        cur_min = bucket_start(now, "1")

        n_min = MAX_MINUTE_DAYS * 1440
        ohlc = _walk_backward(self.rng, self.price, n_min, vol=0.0022, cycle=240.0, trend=0.08,
                              rounder=self.round)
        t0 = cur_min - (n_min - 1) * 60
        self.minutes: list[dict] = [
            {"time": t0 + i * 60, "open": o, "high": h, "low": l, "close": c,
             "volume": float(self.rng.randint(500, 20000))}
            for i, (o, h, l, c) in enumerate(ohlc)
        ]

        # 일봉: 평일만, 오늘(또는 직전 평일)까지
        day = bucket_start(now, "D")
        days: list[int] = []
        d = day
        while len(days) < DAILY_COUNT:
            wd = ((d // 86400) + 3) % 7  # 1970-01-01 은 목요일 -> 0=월요일
            if wd < 5:
                days.append(d)
            d -= 86400
        days.reverse()
        dohlc = _walk_backward(self.rng, self.price, len(days), vol=0.018, cycle=25.0, trend=0.15,
                               rounder=self.round)
        self.daily: list[dict] = [
            {"time": t, "open": o, "high": h, "low": l, "close": c,
             "volume": float(self.rng.randint(200_000, 5_000_000))}
            for t, (o, h, l, c) in zip(days, dohlc)
        ]

    def apply_tick(self, t: int, p: float, v: float) -> None:
        self.price = p
        for bars, tf in ((self.minutes, "1"), (self.daily, "D")):
            s = bucket_start(t, tf)
            last = bars[-1]
            if last["time"] == s:
                last["high"] = max(last["high"], p)
                last["low"] = min(last["low"], p)
                last["close"] = p
                last["volume"] += v
            elif s > last["time"]:
                bars.append({"time": s, "open": p, "high": p, "low": p, "close": p, "volume": v})

    def next_tick(self) -> tuple[int, float, float]:
        ts = 0.01 * self.rng.randint(1, 5) if self.overseas else tick_size(self.price)
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

    def __init__(self) -> None:
        self._symbols: dict[str, _MockSymbol] = {}
        self.hub = MockHub(self)

    def symbol(self, code: str) -> _MockSymbol:
        if code not in self._symbols:
            self._symbols[code] = _MockSymbol(code)
        return self._symbols[code]

    async def minute_bars(self, code: str, days: int, tf: str = "1") -> list[dict]:
        sym = self.symbol(code)
        n = min(days, MAX_MINUTE_DAYS) * 1440
        bars = [dict(b) for b in sym.minutes[-n:]]
        return bars if tf == "1" else aggregate(bars, tf)

    async def period_bars(self, code: str, tf: str, count: int) -> list[dict]:
        daily = [dict(b) for b in self.symbol(code).daily]
        bars = daily if tf == "D" else aggregate(daily, tf)
        return bars[-count:]

    async def close(self) -> None:
        await self.hub.close()
