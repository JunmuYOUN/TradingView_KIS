"""KIS Tradingview 프로토타입 서버.

실행:  python -m server.main   (또는 run.bat / run.sh)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bars import MINUTE_TFS, PERIOD_TFS, aggregate
from .config import DATA_DIR, WEB_DIR, load_settings
from .mock_source import MockSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

settings = load_settings()
CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
DRAW_DIR = DATA_DIR / "drawings"

# 분봉 타임프레임별 기본 조회 일수 (KIS 호출 수를 줄이기 위해 짧게 잡았습니다)
DEFAULT_DAYS = {"1": 1, "3": 2, "5": 3, "15": 5, "30": 8, "60": 15}
DEFAULT_COUNT = {"D": 400, "W": 300, "M": 200}


def make_source():
    if settings.source == "kis":
        from .kis_source import KisSource
        return KisSource(settings)
    return MockSource()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.source = make_source()
    log.info("데이터 소스: %s (env=%s, market=%s)", app.state.source.name, settings.env, settings.market)
    url = f"http://{settings.host}:{settings.port}"
    log.info("브라우저에서 %s 를 여세요", url)
    if os.getenv("OPEN_BROWSER") == "1":
        asyncio.get_running_loop().call_later(1.0, webbrowser.open, url)
    yield
    await app.state.source.close()


app = FastAPI(title="KIS Tradingview", lifespan=lifespan)


def check_code(code: str) -> str:
    code = code.strip().upper()
    if not CODE_RE.match(code):
        raise HTTPException(400, "종목코드는 6자리여야 합니다 (예: 005930)")
    return code


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    log.exception("요청 처리 실패: %s", request.url.path)
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.get("/api/config")
async def api_config():
    return {"source": settings.source, "env": settings.env, "market": settings.market,
            "ws_tr_id": settings.ws_tr_id if settings.source == "kis" else None}


@app.get("/api/candles")
async def api_candles(code: str, tf: str = "D", days: int | None = Query(None, ge=1, le=60),
                      count: int | None = Query(None, ge=10, le=3000)):
    code = check_code(code)
    src = app.state.source
    if tf in MINUTE_TFS:
        d = days or DEFAULT_DAYS[tf]
        bars = await src.minute_bars(code, d)
        if tf != "1":
            bars = aggregate(bars, tf)
    elif tf in PERIOD_TFS:
        bars = await src.period_bars(code, tf, count or DEFAULT_COUNT[tf])
    else:
        raise HTTPException(400, f"지원하지 않는 타임프레임: {tf}")
    return {"code": code, "tf": tf, "bars": bars}


@app.get("/api/drawings/{code}")
async def get_drawings(code: str):
    code = check_code(code)
    p = DRAW_DIR / f"{code}.json"
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
    p = DRAW_DIR / f"{code}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"code": code, "drawings": drawings}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(p)
    return {"ok": True, "count": len(drawings)}


@app.websocket("/ws/ticks")
async def ws_ticks(ws: WebSocket, code: str):
    await ws.accept()
    try:
        code = check_code(code)
    except HTTPException as e:
        await ws.send_json({"type": "status", "state": "error", "msg": e.detail})
        await ws.close()
        return
    hub = app.state.source.hub
    q = await hub.subscribe(code)

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
        for t in tasks:
            t.cancel()
        await hub.unsubscribe(code, q)


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
