"""로컬 차트 서버(HTTP·실시간 웹소켓)에 들어오는 요청을 거릅니다.

- Host 검사: DNS 리바인딩으로 다른 사이트가 127.0.0.1 서버에 붙는 것을 막습니다.
- Origin / Sec-Fetch-Site 검사: 브라우저에 열린 다른 웹사이트가 실시간 웹소켓이나 API 를
  몰래 호출하는 것(Cross-Site WebSocket Hijacking, CSRF)을 막습니다.
- 세션 쿠키(데스크톱 앱): 실행할 때마다 새로 만든 일회용 토큰으로 앱 창에만 쿠키를 주고,
  쿠키가 없는 요청(같은 PC 의 다른 사용자·프로그램)은 거부합니다.
"""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import PlainTextResponse, RedirectResponse

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _same(presented: str, expected: str) -> bool:
    # 바이트로 비교해야 비ASCII 값이 들어와도 예외 없이 일정 시간에 비교합니다
    return hmac.compare_digest(presented.encode("utf-8", "replace"), expected.encode("utf-8"))


class LocalGuard:
    def __init__(self, app, *, host: str, port: int, require_session: bool = False) -> None:
        self.app = app
        if host in LOOPBACK_HOSTS:
            self.hosts: set[str] | None = {f"{name}:{port}" for name in ("127.0.0.1", "localhost", "[::1]")}
        else:
            self.hosts = None  # 사용자가 외부 접속(HOST=0.0.0.0 등)을 연 경우에는 Host 를 고정하지 않습니다
        self.cookie = f"tv_session_{port}"
        self.session = secrets.token_urlsafe(32) if require_session else None
        self._launch = secrets.token_urlsafe(32) if require_session else None

    def launch_url(self, base: str) -> str:
        """앱 창이 처음 열 주소. 한 번 쓰면 더는 통하지 않습니다."""
        return f"{base}/?launch={self._launch}" if self._launch else base

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if self.hosts is not None and host not in self.hosts:
            await self._deny(scope, receive, send, 400, "허용되지 않은 Host 입니다")
            return
        origin = headers.get("origin")
        if origin is not None and origin not in (f"http://{host}", f"https://{host}"):
            await self._deny(scope, receive, send, 403, "다른 사이트에서 온 요청은 받지 않습니다")
            return
        if headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            await self._deny(scope, receive, send, 403, "다른 사이트에서 온 요청은 받지 않습니다")
            return
        if self.session is not None:
            if scope["type"] == "http" and scope["path"] == "/" and self._launch:
                query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
                token = query.get("launch", [""])[0]
                if token and _same(token, self._launch):
                    self._launch = None
                    response = RedirectResponse("/", status_code=303)
                    response.set_cookie(self.cookie, self.session, httponly=True, samesite="strict", path="/")
                    await response(scope, receive, send)
                    return
            presented = cookie_parser(headers.get("cookie", "")).get(self.cookie, "")
            if not _same(presented, self.session):
                await self._deny(scope, receive, send, 403, "이 앱 창에서만 열 수 있습니다")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, status: int, text: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})  # 핸드셰이크 단계에서 403 으로 거절됩니다
            return
        await PlainTextResponse(text, status_code=status, headers={"cache-control": "no-store"})(scope, receive, send)
