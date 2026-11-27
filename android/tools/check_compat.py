"""데스크톱 코드가 안드로이드 앱에서도 도는지 PC 에서 미리 확인합니다.

안드로이드 앱과 같은 Python 버전·같은 패키지(requirements-android.txt)로, 앱이 쓰는
연결부(android_app/bootstrap.py)를 그대로 거쳐 데스크톱의 차트 서버를 띄운 뒤
화면과 API 를 호출해 봅니다. 데스크톱을 고친 것이 안드로이드에서 깨진다면 APK 를
만들기 전에 여기서 걸립니다.

    .venv-compat\\Scripts\\python tools\\check_compat.py
    .venv-compat\\Scripts\\python tools\\check_compat.py --serve   (브라우저로 미리 보기)

실제 KIS 에는 접속하지 않습니다. 임시 폴더와 MOCK 시세만 쓰고, 종목 목록도 내려받지 않습니다.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
ANDROID_PYTHON = PROJECT / "app" / "src" / "main" / "python"
OVERLAY = PROJECT / "app" / "src" / "main" / "assets" / "overlay"

# 안드로이드 화면 보정(overlay/android.js, android.css)이 기대하는 데스크톱 화면의 요소
REQUIRED_IDS = ("chart", "view-tabs", "chart-tab", "settings-tab", "top", "tools", "symbol",
                "settings-view", "alert-notice")
# 얼러트를 휴대폰 알림으로 보내는 보정이 읽는, 데스크톱의 얼러트 저장 키 (web/js/chart-actions.js)
ALERT_STORE_KEY = "kis.localPriceAlerts.v1"

# 안드로이드의 Python 에는 없는 모듈 (최상위에서 조건 없이 불러오면 앱이 시작되지 않습니다)
WINDOWS_ONLY = {"msvcrt", "winreg", "winsound", "_winapi", "nt", "ctypes.wintypes", "webview", "clr", "System",
                "win32api", "win32con", "pythoncom", "pywintypes"}

failures: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> bool:
    print(("  ok    " if ok else "  FAIL  ") + what + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(what)
    return ok


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fake_protect(data: bytes) -> bytes:
    # 검사용. 실제 앱은 Android Keystore 로 암호화합니다.
    return bytes(byte ^ 0x5A for byte in data)[::-1]


def fake_unprotect(data: bytes) -> bytes:
    return bytes(byte ^ 0x5A for byte in data[::-1])


def check_imports(desktop: Path) -> None:
    """이 검사는 Windows 에서 돌기 때문에, Windows 전용 모듈을 불러와도 여기서는 실패하지 않습니다.
    안드로이드에는 없는 모듈을 조건 없이 불러오는 곳이 있는지 소스에서 직접 찾습니다."""
    print("안드로이드에 없는 모듈")
    sources = sorted((desktop / "server").glob("*.py")) + [desktop / "desktop_login.py"]
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        found = []
        for node in tree.body:  # 함수나 if 안에서 불러오는 것은 실행 조건이 있으므로 보지 않습니다
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""] + [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            found += [name for name in names
                      if name in WINDOWS_ONLY or name.split(".")[0] in WINDOWS_ONLY]
        check(not found, f"{source.name}", "Windows 전용: " + ", ".join(found))


def check_static(desktop: Path) -> None:
    print("화면 구조")
    html = (desktop / "web" / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    for name in REQUIRED_IDS:
        check(name in ids, f'index.html 에 id="{name}" 가 있음')
    app_js = (desktop / "web" / "js" / "app.js").read_text(encoding="utf-8")
    check("window.__app" in app_js and "drawings" in app_js,
          "app.js 가 window.__app.drawings 를 내놓음 (터치로 선 그리기)")
    scripts = "".join(path.read_text(encoding="utf-8") for path in (desktop / "web" / "js").glob("*.js"))
    overlay_js = (OVERLAY / "android.js").read_text(encoding="utf-8") if (OVERLAY / "android.js").is_file() else ""
    check(f"'{ALERT_STORE_KEY}'" in scripts and f"'{ALERT_STORE_KEY}'" in overlay_js,
          f"얼러트 저장 키가 데스크톱과 보정 스크립트에서 같음 ({ALERT_STORE_KEY})")
    check("byId('alert-notice')" in scripts and "notice.textContent" in scripts,
          "얼러트가 울리면 #alert-notice 에 문구를 씀 (휴대폰 알림으로 전달)")
    for name in ("android.js", "android.css"):
        check((OVERLAY / name).is_file(), f"overlay/{name} 가 있음")


async def check_server(bootstrap, launch_url: str) -> None:
    import httpx
    import websockets

    base = bootstrap.base_url()
    print("화면 열기와 접근 제한")
    async with httpx.AsyncClient(timeout=20) as outsider:
        denied = await outsider.get(base + "/api/config")
        check(denied.status_code == 403, "쿠키 없는 요청은 거부", f"HTTP {denied.status_code}")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        page = await client.get(launch_url)
        check(page.status_code == 200 and 'id="chart"' in page.text, "일회용 주소로 화면이 열림", f"HTTP {page.status_code}")
        cookie = next(iter(client.cookies.jar), None)
        check(cookie is not None, "세션 쿠키를 받음")
        async with httpx.AsyncClient(timeout=20) as replay:
            again = await replay.get(launch_url)
        check(again.status_code == 403, "일회용 주소는 한 번만 통함", f"HTTP {again.status_code}")

        print("정적 파일")
        for path in ("/js/app.js", "/css/style.css", "/vendor/lightweight-charts.standalone.production.js",
                     "/toolbar-icons.svg"):
            response = await client.get(base + path)
            check(response.status_code == 200 and len(response.content) > 100, path, f"HTTP {response.status_code}")

        print("API")
        config = (await client.get(base + "/api/config")).json()
        check(config.get("source") == "mock" and config.get("desktop") is False, "/api/config", json.dumps(config))
        for tf in ("D", "5"):
            response = await client.get(base + "/api/candles", params={"code": "005930", "tf": tf})
            bars = response.json().get("bars", []) if response.status_code == 200 else []
            check(len(bars) > 10 and {"time", "open", "high", "low", "close"} <= set(bars[0]),
                  f"/api/candles tf={tf}", f"HTTP {response.status_code}, {len(bars)}개")
        origin = {"Origin": base}
        saved = await client.put(base + "/api/drawings/005930", headers=origin,
                                 json={"drawings": [{"id": "t", "type": "hline", "points": []}]})
        loaded = (await client.get(base + "/api/drawings/005930")).json()
        check(saved.status_code == 200 and len(loaded.get("drawings", [])) == 1, "/api/drawings 저장·읽기")
        symbols = await client.get(base + "/api/symbols", params={"q": "삼성"})
        check(symbols.status_code == 200 and "items" in symbols.json(), "/api/symbols", f"HTTP {symbols.status_code}")
        market = await client.put(base + "/api/settings/market", headers=origin, json={"market": "UN"})
        check(market.status_code == 200 and market.json().get("market") == "UN", "/api/settings/market")
        cross = await client.put(base + "/api/settings/market", headers={"Origin": "http://evil.example"},
                                 json={"market": "J"})
        check(cross.status_code == 403, "다른 사이트에서 온 요청은 거부", f"HTTP {cross.status_code}")

        print("실시간 웹소켓 (MOCK)")
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:{bootstrap.PORT}/ws/ticks?code=005930",
                additional_headers={"Cookie": f"{cookie.name}={cookie.value}", "Origin": base},
            ) as ws:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            check(isinstance(message, dict) and "type" in message, "/ws/ticks 에서 메시지 수신")
        except Exception as exc:  # 검사 도구이므로 어떤 실패든 결과로 남깁니다
            check(False, "/ws/ticks 에서 메시지 수신", f"{type(exc).__name__}: {exc}")


def check_account(bootstrap, data_root: Path) -> None:
    from server import config, vault

    print("계정과 암호화 저장")
    check(Path(config.DATA_DIR) == data_root / "data", "저장 위치가 앱 전용 폴더로 바뀜", str(config.DATA_DIR))
    secret_file = data_root / "data" / "probe.bin"
    saved = vault.save(secret_file, {"k": "값"})
    check(saved and vault.load(secret_file) == {"k": "값"}, "vault 저장·읽기")
    check("값".encode() not in secret_file.read_bytes(), "저장 파일에 평문이 없음")
    secret_file.unlink(missing_ok=True)

    status = json.loads(bootstrap.status())
    check(status == {"source": "mock", "env": status.get("env"), "market": "UN", "has_key": False},
          "연결 상태 조회", json.dumps(status))
    message = bootstrap.login("", "")
    check("App Key" in message, "빈 키로 로그인하면 안내 문구", message)
    check(bootstrap.logout() == "", "로그아웃")
    check((data_root / "data" / "kis_credentials.bin").is_file(), "로그아웃 상태가 저장됨")


def add_overlay_route() -> None:
    """미리 보기용: 앱이 WebView 에 넣는 것과 같은 보정 스크립트를 /__overlay.js 로 내줍니다."""
    from starlette.responses import Response
    from starlette.routing import Route

    from server.main import app

    def overlay(_request):
        css = (OVERLAY / "android.css").read_text(encoding="utf-8")
        script = (OVERLAY / "android.js").read_text(encoding="utf-8")
        body = f"window.__kisAndroidCss = {json.dumps(css)};\n{script}"
        return Response(body, media_type="text/javascript", headers={"cache-control": "no-store"})

    app.router.routes.insert(0, Route("/__overlay.js", overlay))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--desktop", type=Path, default=PROJECT.parent,
                        help="데스크톱 프로젝트 폴더")
    parser.add_argument("--serve", action="store_true", help="검사 대신 서버를 켜 두고 주소를 출력")
    parser.add_argument("--port", type=int, default=0, help="--serve 에서 쓸 포트 (기본: 빈 포트)")
    args = parser.parse_args()
    desktop = args.desktop.resolve()
    if not (desktop / "server" / "main.py").is_file():
        print(f"데스크톱 프로젝트를 찾지 못했습니다: {desktop}")
        return 2

    # 실제 KIS 키나 사용자의 .env 를 건드리지 않도록 MOCK 으로 고정합니다.
    for name in ("KIS_APP_KEY", "KIS_APP_SECRET"):
        os.environ.pop(name, None)
    os.environ["DATA_SOURCE"] = "mock"
    os.environ["SYMBOL_DOWNLOAD"] = "off"
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(desktop), str(ANDROID_PYTHON)]

    from android_app import bootstrap

    bootstrap.PORT = args.port or free_port()
    with tempfile.TemporaryDirectory(prefix="kis-android-check-", ignore_cleanup_errors=True) as temp:
        data_root = Path(temp)
        if args.serve:
            print(bootstrap.launch(str(data_root), fake_protect, fake_unprotect), flush=True)
            add_overlay_route()
            print("모바일 보정을 입히려면 브라우저 콘솔에서: "
                  "document.head.appendChild(Object.assign(document.createElement('script'), {src: '/__overlay.js'}))",
                  flush=True)
            print("끝내려면 Ctrl+C 를 누르세요.", flush=True)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
            bootstrap.stop()
            return 0

        print(f"Python {sys.version.split()[0]}, 데스크톱: {desktop}")
        check_imports(desktop)
        check_static(desktop)
        try:
            launch_url = bootstrap.launch(str(data_root), fake_protect, fake_unprotect)
            for noisy in ("httpx", "httpcore", "uvicorn", "uvicorn.error", "app", "websockets"):
                logging.getLogger(noisy).setLevel(logging.WARNING)  # 검사 결과만 보이게
            asyncio.run(check_server(bootstrap, launch_url))
            check_account(bootstrap, data_root)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            check(False, "차트 서버 실행", f"{type(exc).__name__}: {exc}")
        finally:
            bootstrap.stop()

    if failures:
        print(f"\n실패 {len(failures)}건. 데스크톱 변경이 안드로이드 앱과 맞지 않습니다:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n모두 통과했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
