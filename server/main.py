"""KIS Tradingview 프로토타입 서버.

실행:  python -m server.main   (또는 run.bat / run.sh)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import codes
from .bars import MINUTE_TFS, PERIOD_TFS
from .config import DATA_DIR, WEB_DIR, Settings, load_settings, remove_legacy_plaintext
from .guard import LocalGuard
from .history import SearchHistory
from .kis_source import KisError
from .mock_source import MockSource
from .symbols import SymbolBook, clean_name

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
        from .kis_source import KisSource
        return KisSource(settings)
    return MockSource()


@asynccontextmanager
async def lifespan(app: FastAPI):
    remove_legacy_plaintext()  # 이전 버전이 평문으로 남긴 토큰 캐시를 지웁니다
    settings = getattr(app.state, "initial_settings", None) or load_settings()
    app.state.settings = settings
    app.state.source = make_source(settings)
    app.state.event_loop = asyncio.get_running_loop()
    app.state.ws_clients = 0
    app.state.history = SearchHistory(DATA_DIR / "search_history.json")
    app.state.symbols = SymbolBook(DATA_DIR / "symbols.json", download=settings.symbol_download)
    for item in app.state.history.load():  # 종목 목록을 못 받아도 예전에 찾은 종목은 이름으로 찾을 수 있게
        app.state.symbols.remember(item["code"], item["name"])
    app.state.symbols.refresh_if_stale()  # 종목 마스터는 백그라운드에서 받습니다
    log.info("데이터 소스: %s (env=%s, market=%s)", app.state.source.name, settings.env, settings.market)
    if browser_url := getattr(app.state, "browser_url", None):
        asyncio.get_running_loop().call_later(1.0, webbrowser.open, browser_url)
    yield
    await app.state.symbols.close()
    await app.state.source.close()


app = FastAPI(title="KIS Tradingview", lifespan=lifespan)


def check_code(code: str) -> str:
    normalized = codes.normalize(code)
    if not normalized:
        raise HTTPException(400, "종목코드는 6자리(예: 005930) 또는 거래소:티커(예: NAS:AAPL) 형식이어야 합니다")
    return normalized


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
            "ws_tr_id": settings.ws_tr_id if settings.source == "kis" else None}


async def json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "JSON 본문이 필요합니다") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON 객체가 필요합니다")
    return body


@app.get("/api/symbols")
async def api_symbols(q: str = Query("", max_length=40), limit: int = Query(20, ge=1, le=50)):
    """종목명 일부, 코드 앞자리, 초성(ㅅㅅㅈㅈ)으로 종목을 찾습니다."""
    book = app.state.symbols
    await book.wait_ready(5.0)  # 처음 실행해서 종목 목록을 받는 중이면 잠시 기다립니다
    recent = {it["code"] for it in app.state.history.load()}  # 전에 찾아본 종목을 앞에 둡니다
    return {"items": [s.as_dict() for s in book.search(q, limit, recent)], "ready": book.ready}


@app.get("/api/symbols/{code}")
async def api_symbol(code: str):
    code = check_code(code)
    book = app.state.symbols
    await book.wait_ready(3.0)
    s = book.get(code)
    return s.as_dict() if s else {"code": code, "name": "", "market": "", "kind": ""}


@app.get("/api/history")
async def get_history():
    book = app.state.symbols
    items = app.state.history.load()
    for it in items:  # 저장할 때 이름을 몰랐던 종목은 지금 목록에서 채웁니다
        if not it["name"] and (s := book.get(it["code"])):
            it["name"] = s.name
    return {"items": items}


@app.post("/api/history")
async def add_history(request: Request):
    body = await json_body(request)
    code = check_code(str(body.get("code", "")))
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


@app.get("/api/candles")
async def api_candles(code: str, tf: str = "D", days: int | None = Query(None, ge=1, le=60),
                      count: int | None = Query(None, ge=10, le=3000)):
    code = check_code(code)
    src = app.state.source
    if tf in MINUTE_TFS:
        d = days or DEFAULT_DAYS[tf]
        bars = await src.minute_bars(code, d, tf)  # 데이터 소스가 tf 분봉으로 묶어서 돌려줍니다
    elif tf in PERIOD_TFS:
        bars = await src.period_bars(code, tf, count or DEFAULT_COUNT[tf])
    else:
        raise HTTPException(400, f"지원하지 않는 타임프레임: {tf}")
    return {"code": code, "tf": tf, "bars": bars}


@app.get("/api/drawings/{code}")
async def get_drawings(code: str):
    code = check_code(code)
    p = DRAW_DIR / f"{codes.file_stem(code)}.json"
    if not p.exists():
        return {"code": code, "drawings": []}
    return json.loads(p.read_text(encoding="utf-8"))


@app.put("/api/drawings/{code}")
async def put_drawings(code: str, request: Request):
    code = check_code(code)
    body = await request.json()
    drawings = body.get("drawings")
    if not isinstance(drawings, list):
        raise HTTPException(400, "drawings 배열이 필요합니다")
    DRAW_DIR.mkdir(parents=True, exist_ok=True)
    p = DRAW_DIR / f"{codes.file_stem(code)}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"code": code, "drawings": drawings}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(p)
    return {"ok": True, "count": len(drawings)}


@app.websocket("/ws/ticks")
async def ws_ticks(ws: WebSocket, code: str):
    if app.state.ws_clients >= MAX_WS_CLIENTS:
        await ws.close(code=1013)  # 잠시 후 다시 시도
        return
    await ws.accept()
    try:
        code = check_code(code)
    except HTTPException as e:
        await ws.send_json({"type": "status", "state": "error", "msg": e.detail})
        await ws.close()
        return
    app.state.ws_clients += 1
    hub = app.state.source.hub
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


def main() -> None:
    import uvicorn
    settings = load_settings()
    app.state.initial_settings = settings
    url = f"http://{settings.host}:{settings.port}"
    log.info("브라우저에서 %s 를 여세요", url)
    if os.getenv("OPEN_BROWSER") == "1":
        app.state.browser_url = url
    guarded = LocalGuard(app, host=settings.host, port=settings.port)
    uvicorn.run(guarded, host=settings.host, port=settings.port, log_level="info",
                ws_max_size=WS_CLIENT_MAX_MESSAGE)


if __name__ == "__main__":
    main()
