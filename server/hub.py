"""종목별 실시간 메시지를 여러 브라우저 연결로 나눠 주는 허브."""
from __future__ import annotations

import asyncio
from collections import defaultdict


class TickHub:
    """subscribe(code) 로 큐를 받고, publish(code, msg) 로 모든 구독자에게 보냅니다.

    하위 클래스는 on_first_subscribe / on_last_unsubscribe 에서
    실제 데이터 소스(KIS WebSocket, mock 생성기)의 구독을 켜고 끕니다.
    """

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    @property
    def codes(self) -> list[str]:
        return [c for c, s in self._subs.items() if s]

    async def subscribe(self, code: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        first = not self._subs[code]
        self._subs[code].add(q)
        if first:
            await self.on_first_subscribe(code)
        return q

    async def unsubscribe(self, code: str, q: asyncio.Queue) -> None:
        s = self._subs.get(code)
        if not s:
            return
        s.discard(q)
        if not s:
            self._subs.pop(code, None)
            await self.on_last_unsubscribe(code)

    def publish(self, code: str, msg: dict) -> None:
        for q in list(self._subs.get(code, ())):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # 느린 브라우저는 일부 틱을 건너뜁니다

    def publish_all(self, msg: dict) -> None:
        for code in self.codes:
            self.publish(code, msg)

    async def on_first_subscribe(self, code: str) -> None:  # pragma: no cover
        pass

    async def on_last_unsubscribe(self, code: str) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:  # pragma: no cover
        pass
