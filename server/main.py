"""KIS Tradingview 프로토타입 서버.

실행:  python -m server.main   (또는 run.bat / run.sh)
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import codes
from .drawing_store import read_drawings, valid_drawings, write_drawings
from .bars import MINUTE_TFS, PERIOD_TFS, dedupe_sort, kst_epoch
from .config import DATA_DIR, MARKETS, WEB_DIR, Settings, load_settings, remove_legacy_plaintext, save_market
from .guard import LOOPBACK_HOSTS, LocalGuard
from .history import SearchHistory
from .kis_source import KisError, KisSource
from .mock_source import MockSource
from .symbols import SymbolBook, clean_name
from .yahoo_lookup import YahooLookupUnavailable
from .yahoo_source import YahooError, YahooSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

DRAW_DIR = DATA_DIR / "drawings"
MAX_WS_CLIENTS = 16          # 동시에 붙을 수 있는 실시간 웹소켓 수
WS_CLIENT_MAX_MESSAGE = 4096  # 화면은 웹소켓으로 거의 보내는 것이 없으므로 작게 제한합니다

# 분봉 타임프레임별 기본 조회 일수 (KIS 호출 수를 줄이기 위해 짧게 잡았습니다)
DEFAULT_DAYS = {"1": 1, "3": 2, "5": 3, "15": 5, "30": 8, "60": 15}
DEFAULT_COUNT = {"D": 400, "W": 300, "M": 200}


def make_source(settings: Settings):
    if settings.source == "kis":
        return KisSource(settings)
    return MockSource()


@asynccontextmanager
async def lifespan(app: FastAPI):
    remove_legacy_plaintext()  # 이전 버전이 평문으로 남긴 토큰 캐시를 지웁니다
    settings = getattr(app.state, "initial_settings", None) or load_settings()
    app.state.settings = settings
    app.state.source = make_source(settings)
    app.state.yahoo = YahooSource()
    app.state.event_loop = asyncio.get_running_loop()
    app.state.connection_lock = asyncio.Lock()
    app.state.ws_clients = 0
    app.state.history = SearchHistory(DATA_DIR / "search_history.json")
    download_symbols = settings.symbol_download and (not getattr(app.state, "desktop", False) or settings.source == "kis")
    app.state.symbols = SymbolBook(DATA_DIR / "symbols.json", download=download_symbols)
    for item in app.state.history.load():  # 종목 목록을 못 받아도 예전에 찾은 종목은 이름으로 찾을 수 있게
        app.state.symbols.remember(item["code"], item["name"])
    app.state.symbols.refresh_if_stale()  # 종목 마스터는 백그라운드에서 받습니다
    log.info("데이터 소스: %s (env=%s, market=%s)", app.state.source.name, settings.env, settings.market)
    del settings  # The lifespan generator must not retain credentials after logout.
    if browser_url := getattr(app.state, "browser_url", None):
        del app.state.browser_url
        loop = asyncio.get_running_loop()
        loop.call_later(0.5, loop.run_in_executor, None, open_chart_browser, browser_url)
    yield
    await app.state.symbols.close()
    await app.state.source.close()
    await app.state.yahoo.close()


app = FastAPI(title="KIS Tradingview", lifespan=lifespan)


def check_code(code: str) -> str:
    normalized = codes.normalize(code)
    if not normalized:
        raise HTTPException(400, "종목이나 지수, 선물, 환율을 검색해 목록에서 선택하세요.")
    return normalized


async def check_chart_code(code: str) -> str:
    code = check_code(code)
    if codes.is_yahoo(code):
        try:
            instrument = await app.state.symbols.resolve_yahoo(code)
        except YahooLookupUnavailable:
            raise HTTPException(503, "Yahoo Finance에서 티커를 확인하지 못했습니다. 잠시 후 다시 시도하세요.") from None
        if instrument is None:
            raise HTTPException(404, "Yahoo Finance에서 이 선물·환율·지수를 찾을 수 없습니다.")
    elif codes.is_index(code):
        book = app.state.symbols
        if not book.get(code):
            await book.wait_ready(3.0)
            if not book.get(code):
                raise HTTPException(404, "지수 목록에서 이 코드를 찾을 수 없습니다. 지수 이름으로 검색하세요.")
    return code


def chart_source(code: str):
    if codes.is_yahoo(code):
        return app.state.yahoo, codes.yahoo_ticker(code)
    return app.state.source, code


async def current_quote_status(source, code: str, ticker: str, tf: str):
    if source.name == "yahoo":
        try:
            return await asyncio.wait_for(source.quote_status(ticker), timeout=4)
        except Exception:
            # An unavailable delay indicator must not hide a successful price response.
            log.debug("Yahoo 시세 상태를 확인하지 못했습니다.")
            return None
    if source.name == "kis" and codes.is_us_index(code) and tf in PERIOD_TFS:
        # FHKST03030100 describes the current index value as either delayed or a closing price.
        return {"delayed_or_close": True}
    if source.name == "kis" and codes.is_overseas(code):
        return {"delay_minutes": 0}  # KIS specifies a zero-minute delay on these US stock feeds.
    return None


@app.exception_handler(YahooError)
async def yahoo_error(request: Request, exc: YahooError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    log.exception("요청 처리 실패: %s", request.url.path)
    # KIS 오류 메시지만 화면에 보여 주고, 그 밖의 내부 오류 내용은 로그에만 남깁니다
    detail = str(exc) if isinstance(exc, KisError) else "데이터를 불러오지 못했습니다. 잠시 후 다시 시도하세요."
    return JSONResponse({"detail": detail}, status_code=502)


@app.get("/api/config")
async def api_config(request: Request):
    settings = request.app.state.settings
    return {"source": settings.source, "env": settings.env, "market": settings.market,
            "desktop": getattr(request.app.state, "desktop", False),
            "quote_transport": "https" if settings.source == "kis" else "mock", "ws_tr_id": None}


async def json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "JSON 본문이 필요합니다") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON 객체가 필요합니다")
    return body


@app.put("/api/settings/market")
async def update_market(request: Request):
    market = (await json_body(request)).get("market")
    if market not in MARKETS:
        raise HTTPException(400, "지원하지 않는 국내 시세 거래소입니다.")
    state = request.app.state
    async with state.connection_lock:
        settings = replace(state.settings, market=market)
        source = state.source
        try:
            if source.name == "kis" and market != state.settings.market:
                # 진행 중인 조회는 기존 거래소의 캐시에만 씁니다. 인증 세션과 호출 제한은 공유합니다.
                source = KisSource(settings, rest=source.rest)
            save_market(market)
        except OSError:
            raise HTTPException(500, "거래소 설정을 저장할 수 없습니다. 폴더 권한을 확인하세요.") from None
        state.source, state.settings, state.initial_settings = source, settings, settings
    return {"market": market}


@app.get("/api/symbols")
async def api_symbols(q: str = Query("", max_length=40), limit: int = Query(20, ge=1, le=50)):
    """종목명 일부, 코드 앞자리, 초성(ㅅㅅㅈㅈ)으로 종목을 찾습니다."""
    book = app.state.symbols
    candidate = codes.yahoo_candidate(q)
    if candidate:
        try:
            await book.resolve_yahoo(candidate)
        except YahooLookupUnavailable:
            if not book.search(q, 1):
                raise HTTPException(503, "Yahoo Finance 티커 검색에 연결하지 못했습니다. 잠시 후 다시 시도하세요.") from None
    else:
        await book.wait_ready(5.0)  # 처음 실행해서 종목 목록을 받는 중이면 잠시 기다립니다
    recent = {it["code"] for it in app.state.history.load()}  # 전에 찾아본 종목을 앞에 둡니다
    return {"items": [s.as_dict() for s in book.search(q, limit, recent)], "ready": book.ready}


@app.get("/api/symbols/{code}")
async def api_symbol(code: str):
    code = await check_chart_code(code)
    book = app.state.symbols
    if not book.get(code):
        await book.wait_ready(3.0)
    s = book.get(code)
    return s.as_dict() if s else {"code": code, "name": "", "market": "", "kind": ""}


@app.get("/api/history")
async def get_history():
    book = app.state.symbols
    items = app.state.history.load()
    for it in items:  # 저장할 때 이름을 몰랐던 종목은 지금 목록에서 채웁니다
        if s := book.get(it["code"]):
            if not it["name"]:
                it["name"] = s.name
            if codes.is_yahoo(s.code):
                it.update(s.as_dict())
            elif codes.is_index(s.code):
                it.update(symbol=s.as_dict()["symbol"], kind=s.kind, market=s.market)
    return {"items": items}


@app.post("/api/history")
async def add_history(request: Request):
    body = await json_body(request)
    code = await check_chart_code(str(body.get("code", "")))
    s = app.state.symbols.get(code)
    name = s.name if s else clean_name(body.get("name", ""))
    app.state.symbols.remember(code, name)
    return {"items": app.state.history.add(code, name)}


@app.delete("/api/history/{code}")
async def delete_history(code: str):
    return {"items": app.state.history.remove(check_code(code))}


@app.delete("/api/history")
async def clear_history():
    return {"items": app.state.history.clear()}


async def while_connected(request: Request, operation):
    """Stop paginated provider calls as soon as the requesting chart goes away."""
    async def disconnected():
        while (await request.receive())["type"] != "http.disconnect":
            pass

    work = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            return work.result()
        raise HTTPException(499, "차트 조회가 취소되었습니다.")
    finally:
        for task in (work, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, watcher, return_exceptions=True)


@app.get("/api/candles")
async def api_candles(request: Request, code: str, tf: str = "D", days: int | None = Query(None, ge=1, le=60),
                      count: int | None = Query(None, ge=10, le=3000),
                      before: int | None = Query(None, ge=kst_epoch("18000101"), le=kst_epoch("21000101")),
                      from_date: int | None = Query(None, ge=kst_epoch("18000101"), le=kst_epoch("21000101"))):
    return await while_connected(request, candle_page(code, tf, days, count, before, from_date))


async def candle_page(code, tf, days, count, before, from_date):
    code = await check_chart_code(code)
    src, ticker = chart_source(code)
    if from_date is not None:
        if src.name != "yahoo" or tf not in PERIOD_TFS or before is None or from_date >= before:
            raise HTTPException(400, "날짜 이동에는 Yahoo 일·주·월봉의 시작일과 그 이후의 종료일이 필요합니다.")
        bars, earliest, has_more = await src.period_prefix(ticker, tf, from_date, before)
        return {"code": code, "tf": tf, "bars": bars, "has_more": has_more,
                "source": src.name, "earliest_available": earliest}
    if tf in MINUTE_TFS:
        index_snapshot = src.name == "kis" and codes.is_index(code)
        d = days or (60 if index_snapshot else DEFAULT_DAYS[tf])
        bars = await src.minute_bars(ticker, d, tf, before=before)
        paper_domestic = src.name == "kis" and app.state.settings.env == "demo" and not codes.is_overseas(code)
        has_more = bool(bars) and not (index_snapshot or paper_domestic)
    elif tf in PERIOD_TFS:
        bars = await src.period_bars(ticker, tf, count or DEFAULT_COUNT[tf], before=before)
        has_more = bool(bars)
    else:
        raise HTTPException(400, f"지원하지 않는 타임프레임: {tf}")
    status = await current_quote_status(src, code, ticker, tf) if before is None and bars else None
    if src is not chart_source(code)[0]:
        raise HTTPException(409, "연결이 변경되었습니다")
    if src.name == "mock" and bars:
        has_more = bars[0]["time"] > src.history_start
    return {"code": code, "tf": tf, "bars": bars, "has_more": has_more, "source": src.name,
            "quote_status": status}


async def quote_update(source, ticker, tf, since, before):
    """Return the live tail, or one backward recovery page ending before the cursor."""
    snapshot = since is not None and source.name == "kis" and codes.is_index(ticker) and tf in MINUTE_TFS
    if before is not None:
        latest = []
    elif snapshot:
        # This TR has no paging cursor. Only a fresh, contiguous provider window can prove overlap.
        latest = await source.index_recovery_bars(ticker, tf)
    elif since is not None and source.name == "yahoo":
        latest = await source.recent_bars(ticker, tf, since=since)
    else:
        latest = await source.recent_bars(ticker, tf)
    if since is None:
        return latest, None, None
    if before is None and (not latest or latest[-1]["time"] < since):
        return [], None, None
    if latest and latest[0]["time"] <= since:
        rows = [b for b in latest if b["time"] >= since]
        if rows[0]["time"] != since:
            return rows, "unavailable", None
        return rows, "backfilled" if len(rows) > 3 else None, None
    if snapshot:
        return latest, "unavailable", None

    cursor = before if before is not None else latest[0]["time"]
    if tf in PERIOD_TFS:
        page = await source.period_bars(ticker, tf, 100, before=cursor)
    else:
        page = await source.minute_bars(ticker, 2, tf, before=cursor)
    older = [b for b in page if b["time"] < cursor]
    window = dedupe_sort(older + latest)
    rows = [b for b in window if b["time"] >= since]
    if rows and rows[0]["time"] == since:
        return rows, "backfilled", None
    if not older or older[0]["time"] <= since:
        return rows, "unavailable", None
    return rows, "backfilled", older[0]["time"]


@app.get("/api/quotes/latest")
async def latest_quotes(request: Request, code: str, tf: str = "D",
                        since: int | None = Query(None, ge=kst_epoch("18000101"), le=kst_epoch("21000101")),
                        before: int | None = Query(None, ge=kst_epoch("18000101"), le=kst_epoch("21000101"))):
    if before is not None and (since is None or since >= before):
        raise HTTPException(400, "복구 시작 시각은 종료 시각보다 빨라야 합니다.")
    code = await check_chart_code(code)
    if tf not in MINUTE_TFS and tf not in PERIOD_TFS:
        raise HTTPException(400, "지원하지 않는 타임프레임입니다")
    source, ticker = chart_source(code)
    if source.name == "mock":
        raise HTTPException(409, "한투 API 로그인이 필요합니다")
    bars, recovery, next_before = await while_connected(request, quote_update(source, ticker, tf, since, before))
    status = await current_quote_status(source, code, ticker, tf) if before is None else None
    if source is not chart_source(code)[0]:
        raise HTTPException(409, "연결이 변경되었습니다")
    return {"bars": bars, "source": source.name, "quote_status": status, "recovery": recovery, "next_before": next_before}


@app.get("/api/drawings/{code}")
async def get_drawings(code: str):
    code = check_code(code)
    p = DRAW_DIR / f"{codes.file_stem(code)}.json"
    return await asyncio.to_thread(read_drawings, p, code)


@app.put("/api/drawings/{code}")
async def put_drawings(code: str, request: Request):
    code = check_code(code)
    if codes.is_yahoo(code):
        await check_chart_code(code)
    body = await json_body(request)
    drawings = body.get("drawings")
    if not valid_drawings(drawings):
        raise HTTPException(400, "drawings 배열이 필요합니다")
    expected = body.get("base_revision")
    if expected is not None and not isinstance(expected, str):
        raise HTTPException(400, "저장 버전을 확인할 수 없습니다.")
    p = DRAW_DIR / f"{codes.file_stem(code)}.json"
    result, accepted = await asyncio.to_thread(write_drawings, p, code, drawings, expected)
    if not accepted:
        return JSONResponse({"detail": "서버의 그림이 변경되었습니다.", **result}, status_code=409)
    return {"ok": True, "count": len(drawings), "revision": result["revision"]}


@app.websocket("/ws/ticks")
async def ws_ticks(ws: WebSocket, code: str):
    source = app.state.source
    if source.name != "mock" or codes.is_yahoo(codes.normalize(code) or ""):
        await ws.close(code=1008)
        return
    if app.state.ws_clients >= MAX_WS_CLIENTS:
        await ws.close(code=1013)  # 잠시 후 다시 시도
        return
    await ws.accept()
    try:
        code = await check_chart_code(code)
    except HTTPException as e:
        await ws.send_json({"type": "status", "state": "error", "msg": e.detail})
        await ws.close()
        return
    app.state.ws_clients += 1
    hub = source.hub
    try:
        q = await hub.subscribe(code)
    except BaseException:
        app.state.ws_clients -= 1
        raise

    async def pump():
        while True:
            msg = await q.get()
            await ws.send_json(msg)

    async def drain():  # 브라우저 쪽 종료를 감지합니다
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(pump()), asyncio.create_task(drain())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        app.state.ws_clients -= 1
        for t in tasks:
            t.cancel()
        await hub.unsubscribe(code, q)


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


def open_chart_browser(url: str) -> None:
    try:
        if webbrowser.open(url):
            return
    except webbrowser.Error:
        pass
    print(f"브라우저에서 열 일회용 주소: {url}", flush=True)


def main() -> None:
    import uvicorn
    settings = load_settings()
    host = settings.host.strip().lower()
    cert, key = settings.web_tls_cert, settings.web_tls_key
    if bool(cert) != bool(key) or (host not in LOOPBACK_HOSTS and not cert):
        raise SystemExit("외부 접속에는 HTTPS가 필요합니다. WEB_TLS_CERT와 WEB_TLS_KEY를 함께 설정하세요.")
    public = host
    if host not in LOOPBACK_HOSTS:
        public = settings.web_public_host or (socket.getfqdn() if host in {"0.0.0.0", "::"} else host)
    scheme = "https" if cert else "http"
    app.state.initial_settings = settings
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((host, settings.port))
        port = listener.getsockname()[1]
        guard = LocalGuard(app, host=host, port=port, public_host=public, https=bool(cert))
        config = uvicorn.Config(guard, host=host, port=port, access_log=False, proxy_headers=False,
                                ssl_certfile=cert or None, ssl_keyfile=key or None,
                                ws_max_size=WS_CLIENT_MAX_MESSAGE)
        config.load()
        url_host = f"[{public}]" if ":" in public else public
        url = guard.launch_url(f"{scheme}://{url_host}:{port}")
        if os.getenv("OPEN_BROWSER") == "1":
            app.state.browser_url = url
        else:
            print(f"브라우저에서 열 일회용 주소: {url}", flush=True)
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
