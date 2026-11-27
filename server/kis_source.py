"""한국투자증권 Open API 데이터 소스 (REST 차트 + WebSocket 실시간 체결).

참고: https://github.com/koreainvestment/open-trading-api (examples_llm/domestic_stock)
  - 국내주식기간별시세(일/주/월/년)  FHKST03010100  1회 최대 100건
  - 주식당일분봉조회                 FHKST03010200  1회 최대 30건, 당일만
  - 주식일별분봉조회                 FHKST03010230  1회 최대 120건, 최대 1년
  - 실시간체결가 H0STCNT0(KRX) / H0UNCNT0(통합) / H0NXCNT0(NXT)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from contextlib import suppress
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx
import websockets

from . import vault
from .bars import dedupe_sort, epoch_to_kst_parts as _parts, kst_epoch, now_epoch, now_kst
from .config import DATA_DIR, Settings
from .hub import TickHub

log = logging.getLogger("kis")

QUOTE = "/uapi/domestic-stock/v1/quotations"
MAX_WS_CODES = 40
APPROVAL_TTL = 20 * 3600     # 웹소켓 접속키 재사용 시간 (유효기간 24시간)
APPROVAL_MIN_REISSUE = 60    # 접속키 강제 재발급 최소 간격 (초)
WS_MAX_FRAME = 256 * 1024    # KIS 실시간 메시지 최대 크기
WS_MAX_RECORDS = 100         # 메시지 1개에 담긴 체결 건수 상한
HHMMSS_RE = re.compile(r"^([01]\d|2[0-3])[0-5]\d[0-5]\d$")
DATE_RE = re.compile(r"^20\d{6}$")


class KisError(RuntimeError):
    def __init__(self, code: str, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code


def _num(s) -> float:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0  # "nan"/"inf" 는 JSON 으로 보낼 수 없습니다


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        raise KisError(str(r.status_code), "KIS 서버 응답을 해석할 수 없습니다") from None
    return body if isinstance(body, dict) else {}


def _clean(text, limit: int = 120) -> str:
    """KIS 메시지를 화면에 보내기 전에 제어 문자를 지우고 길이를 줄입니다."""
    return "".join(ch for ch in str(text) if ch.isprintable())[:limit]


def _minus_one_minute(yyyymmdd: str, hhmmss: str) -> tuple[str, str]:
    d = datetime.strptime(yyyymmdd + hhmmss, "%Y%m%d%H%M%S") - timedelta(minutes=1)
    return d.strftime("%Y%m%d"), d.strftime("%H%M%S")


# ---------------------------------------------------------------- 인증
class KisAuth:
    """접근토큰(REST)과 웹소켓 접속키를 암호화 파일(Windows DPAPI)에 캐시해서 재사용합니다.

    접근토큰은 유효기간 1일, 발급은 1분에 1회로 제한되므로 매번 새로 받으면 안 됩니다.
    Windows 가 아니면 디스크에 남기지 않고 메모리에만 둡니다.
    """

    def __init__(self, s: Settings, client: httpx.AsyncClient) -> None:
        self.s = s
        self.client = client
        self._lock = asyncio.Lock()
        self._approval_lock = asyncio.Lock()
        credentials = json.dumps([s.app_key, s.app_secret]).encode("utf-8")
        self._credential_id = sha256(credentials).hexdigest()
        self._token_file = DATA_DIR / f"kis_token_{s.env}.bin"
        self._approval_file = DATA_DIR / f"kis_approval_{s.env}.bin"
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._approval: str | None = None
        self._approval_at: float = 0.0

    def _load(self, p: Path) -> dict:
        d = vault.load(p) or {}
        return d if d.get("credential_id") == self._credential_id else {}

    def _save(self, p: Path, d: dict) -> None:
        d["credential_id"] = self._credential_id
        try:
            vault.save(p, d)
        except OSError as e:
            log.warning("인증 캐시를 암호화해 저장하지 못했습니다 (%s), 이번 실행에서만 사용합니다", type(e).__name__)

    async def token(self, force: bool = False) -> str:
        async with self._lock:
            now = time.time()
            if not force and self._token and now < self._token_exp - 600:
                return self._token
            if not force:
                d = self._load(self._token_file)
                if d.get("access_token") and now < d.get("expires_at", 0) - 600:
                    self._token, self._token_exp = d["access_token"], d["expires_at"]
                    return self._token
            r = await self.client.post(
                f"{self.s.rest_base}/oauth2/tokenP",
                json={"grant_type": "client_credentials",
                      "appkey": self.s.app_key, "appsecret": self.s.app_secret},
            )
            body = _json(r)
            if r.status_code != 200 or not isinstance(body.get("access_token"), str):
                raise KisError(_clean(body.get("error_code") or r.status_code, 20),
                               _clean(body.get("error_description") or "접근토큰 발급 실패"))
            exp_str = body.get("access_token_token_expired")  # "2026-11-26 21:00:00" (KST)
            try:
                exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=now_kst().tzinfo).timestamp()
            except Exception:
                exp = now + int(body.get("expires_in", 86400))
            self._token, self._token_exp = body["access_token"], exp
            self._save(self._token_file, {"access_token": self._token, "expires_at": exp})
            log.info("KIS 접근토큰 발급 완료 (만료 %s)", exp_str)
            return self._token

    async def approval_key(self, force: bool = False) -> str:
        async with self._approval_lock:
            now = time.time()
            if force and now - self._approval_at < APPROVAL_MIN_REISSUE:
                force = False  # 방금 받은 키입니다. 오류가 반복돼도 발급 요청을 몰아 보내지 않습니다
            if not force:
                if self._approval and now - self._approval_at < APPROVAL_TTL:
                    return self._approval
                d = self._load(self._approval_file)
                issued = d.get("issued_at", 0)
                if isinstance(d.get("approval_key"), str) and 0 <= now - issued < APPROVAL_TTL:
                    self._approval, self._approval_at = d["approval_key"], issued
                    return self._approval
            r = await self.client.post(
                f"{self.s.rest_base}/oauth2/Approval",
                json={"grant_type": "client_credentials",
                      "appkey": self.s.app_key, "secretkey": self.s.app_secret},  # secretkey 주의
            )
            body = _json(r)
            if r.status_code != 200 or not isinstance(body.get("approval_key"), str):
                raise KisError(_clean(body.get("error_code") or r.status_code, 20),
                               _clean(body.get("error_description") or "웹소켓 접속키 발급 실패"))
            self._approval, self._approval_at = body["approval_key"], now
            self._save(self._approval_file, {"approval_key": self._approval, "issued_at": now})
            log.info("KIS 웹소켓 접속키 발급 완료")
            return self._approval


# ---------------------------------------------------------------- REST
class KisRest:
    def __init__(self, s: Settings) -> None:
        self.s = s
        # 인증서 검증을 켜 두고, 키가 담긴 요청이 다른 주소로 따라가지 않도록 리다이렉트를 막습니다
        self.client = httpx.AsyncClient(timeout=15.0, verify=True, follow_redirects=False)
        self.auth = KisAuth(s, self.client)
        self._interval = 1.0 / s.rate_per_sec
        self._next_slot = 0.0
        self._rate_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_slot = max(now, self._next_slot) + self._interval

    async def get(self, path: str, tr_id: str, params: dict) -> dict:
        retried_token = False
        for attempt in range(4):
            await self._throttle()
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {await self.auth.token()}",
                "appkey": self.s.app_key,
                "appsecret": self.s.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            r = await self.client.get(f"{self.s.rest_base}{path}", headers=headers, params=params)
            body = _json(r)
            if body.get("rt_cd") == "0":
                return body
            code, msg = _clean(body.get("msg_cd") or r.status_code, 20), _clean(body.get("msg1", ""))
            if code == "EGW00201":            # 초당 거래건수 초과
                await asyncio.sleep(1.0 + attempt)
                continue
            if code in ("EGW00123", "EGW00121") and not retried_token:  # 토큰 만료/무효
                await self.auth.token(force=True)
                retried_token = True
                continue
            raise KisError(code, msg)
        raise KisError("EGW00201", "초당 거래건수 초과가 반복되었습니다")

    async def close(self) -> None:
        await self.client.aclose()


# ---------------------------------------------------------------- 실시간
class KisRealtimeHub(TickHub):
    """KIS 웹소켓 1개 연결을 유지하면서 구독 종목의 체결 틱을 나눠 줍니다.

    KIS 실시간 서버는 TLS(wss)를 지원하지 않아 이 구간은 암호화되지 않습니다. 그래서 받은
    메시지를 그대로 믿지 않고 TR·종목·가격·시각을 검사한 뒤에만 화면으로 보냅니다.
    """

    WATCHDOG_SEC = 75  # 이 시간 동안 아무 메시지(PINGPONG 포함)가 없으면 다시 접속
    STABLE_SEC = 60    # 이만큼 연결이 유지돼야 재접속 대기 시간을 처음 값으로 되돌립니다

    def __init__(self, s: Settings, auth: KisAuth) -> None:
        super().__init__()
        self.s = s
        self.auth = auth
        self._ws = None
        self._task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._connected_once = False

    def _msg(self, key: str, code: str, tr_type: str) -> str:
        return json.dumps({
            "header": {"approval_key": key, "custtype": "P", "tr_type": tr_type, "content-type": "utf-8"},
            "body": {"input": {"tr_id": self.s.ws_tr_id, "tr_key": code}},
        })

    async def _send(self, code: str, tr_type: str) -> None:
        ws = self._ws
        if ws is None:
            return
        key = await self.auth.approval_key()
        async with self._send_lock:
            try:
                await ws.send(self._msg(key, code, tr_type))
            except Exception as e:  # 연결이 끊긴 경우 재접속 루프가 다시 구독합니다
                log.warning("구독 메시지 전송 실패 (%s)", type(e).__name__)

    async def on_first_subscribe(self, code: str) -> None:
        if len(self.codes) > MAX_WS_CODES:
            self.publish(code, {"type": "status", "state": "error",
                                "msg": f"실시간 구독은 세션당 {MAX_WS_CODES}종목까지입니다"})
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        else:
            await self._send(code, "1")

    async def on_last_unsubscribe(self, code: str) -> None:
        await self._send(code, "2")

    def _handle_data(self, raw: str) -> None:
        # 형식: 0|H0STCNT0|003|필드^필드^...   (암호화 데이터는 1| 로 시작, 체결가는 암호화되지 않음)
        parts = raw.split("|", 3)
        if len(parts) < 4 or parts[0] != "0" or parts[1] != self.s.ws_tr_id:
            return  # 구독하지 않은 TR 은 버립니다
        try:
            n = int(parts[2])
        except ValueError:
            return
        if not 1 <= n <= WS_MAX_RECORDS:
            return
        fields = parts[3].split("^")
        width = len(fields) // n
        if width < 13:
            return
        today = now_kst().strftime("%Y%m%d")
        now = now_epoch()
        subscribed = set(self.codes)
        for i in range(n):
            f = fields[i * width:(i + 1) * width]
            code, hhmmss, price, vol = f[0], f[1], _num(f[2]), _num(f[12])
            if code not in subscribed or not HHMMSS_RE.match(hhmmss):
                continue
            if not (0 < price < 1e8 and 0 <= vol < 1e12):
                continue
            date = f[33] if len(f) > 33 and DATE_RE.match(f[33]) else today  # BSOP_DATE
            try:
                t = kst_epoch(date, hhmmss)
            except ValueError:
                continue
            if not now - 3 * 86400 <= t <= now + 3600:
                continue  # 시각이 크게 어긋난 틱은 차트를 망가뜨리므로 버립니다
            self.publish(code, {"type": "tick", "t": t, "p": price, "v": vol})

    async def _run(self) -> None:
        backoff = 1.0
        while self.codes:
            opened_at = None
            try:
                key = await self.auth.approval_key()
                # compression=None: 압축 해제 폭탄을 막고, max_size 로 메시지 크기를 제한합니다
                async with websockets.connect(self.s.ws_url, ping_interval=None, open_timeout=10,
                                              max_size=WS_MAX_FRAME, compression=None) as ws:
                    opened_at = time.monotonic()
                    self._ws = ws
                    for c in self.codes[:MAX_WS_CODES]:
                        await ws.send(self._msg(key, c, "1"))
                    if self._connected_once:
                        self.publish_all({"type": "resync"})  # 끊긴 구간을 REST 로 다시 받도록 알림
                    self._connected_once = True
                    self.publish_all({"type": "status", "state": "live", "msg": f"KIS 실시간 ({self.s.ws_tr_id})"})
                    while self.codes:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self.WATCHDOG_SEC)
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        if raw[:1] in ("0", "1"):
                            self._handle_data(raw)
                            continue
                        try:
                            d = json.loads(raw)
                        except ValueError:
                            continue
                        if not isinstance(d, dict):
                            continue
                        header = d.get("header") if isinstance(d.get("header"), dict) else {}
                        if header.get("tr_id") == "PINGPONG":
                            await ws.pong(raw)  # 공식 예제와 같은 방식으로 응답
                            continue
                        body = d.get("body") if isinstance(d.get("body"), dict) else {}
                        if body.get("rt_cd") not in (None, "0"):
                            msg = _clean(body.get("msg1", ""))
                            log.warning("KIS 웹소켓 응답: %s", msg)
                            code = header.get("tr_key")
                            if code in self.codes:
                                self.publish(code, {"type": "status", "state": "error", "msg": msg})
                            if "approval" in msg.lower() or "appkey" in msg.lower():
                                await self.auth.approval_key(force=True)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                log.warning("KIS 웹소켓 %d초 동안 메시지 없음, 다시 접속합니다", self.WATCHDOG_SEC)
            except Exception as e:
                log.warning("KIS 웹소켓 오류 (%s: %s)", type(e).__name__, _clean(e, 200))
                self.publish_all({"type": "status", "state": "reconnecting",
                                  "msg": "KIS 실시간 연결이 끊겨 다시 접속합니다"})
            finally:
                self._ws = None
            if not self.codes:
                break
            if opened_at is not None and time.monotonic() - opened_at >= self.STABLE_SEC:
                backoff = 1.0  # 접속하자마자 끊기는 경우에는 대기 시간을 계속 늘립니다
            await asyncio.sleep(backoff * random.uniform(0.7, 1.3))
            backoff = min(backoff * 2, 30.0)

    async def close(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task  # 웹소켓이 닫힌 뒤에 HTTP 클라이언트를 닫습니다


# ---------------------------------------------------------------- 데이터 소스
class KisSource:
    name = "kis"

    def __init__(self, s: Settings) -> None:
        self.s = s
        self.rest = KisRest(s)
        self.hub = KisRealtimeHub(s, self.rest.auth)
        self._cache_dir = DATA_DIR / "cache" / s.market
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[tuple[str, str], list[dict]] = {}
        self._today: dict[str, tuple[str, list[dict]]] = {}

    # ---- 일/주/월봉
    async def period_bars(self, code: str, tf: str, count: int) -> list[dict]:
        end = now_kst().strftime("%Y%m%d")
        out: list[dict] = []
        for _ in range(30):
            body = await self.rest.get(f"{QUOTE}/inquire-daily-itemchartprice", "FHKST03010100", {
                "FID_COND_MRKT_DIV_CODE": self.s.market,
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": "19900101",
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": tf,
                "FID_ORG_ADJ_PRC": "0",  # 0: 수정주가
            })
            rows = [r for r in body.get("output2") or [] if r.get("stck_bsop_date")]
            if not rows:
                break
            for r in rows:
                o, h, l, c = (_num(r.get(k)) for k in ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr"))
                if c <= 0:
                    continue
                out.append({"time": kst_epoch(r["stck_bsop_date"]), "open": o or c, "high": h or c,
                            "low": l or c, "close": c, "volume": _num(r.get("acml_vol"))})
            oldest = min(r["stck_bsop_date"] for r in rows)
            if len(out) >= count or len(rows) < 100:
                break
            end = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        return dedupe_sort(out)[-count:]

    # ---- 분봉
    @staticmethod
    def _minute_row(r: dict) -> dict | None:
        d, h = r.get("stck_bsop_date"), r.get("stck_cntg_hour")
        c = _num(r.get("stck_prpr"))
        if not d or not h or c <= 0:
            return None
        o, hi, lo = _num(r.get("stck_oprc")), _num(r.get("stck_hgpr")), _num(r.get("stck_lwpr"))
        # stck_cntg_hour 는 그 1분의 시작 시각입니다 (0905 = 09:05:00~09:05:59)
        return {"time": kst_epoch(d, h[:4] + "00"), "open": o or c, "high": hi or c, "low": lo or c,
                "close": c, "volume": _num(r.get("cntg_vol"))}

    async def _today_minutes(self, code: str) -> list[dict]:
        """당일 1분봉. 한 번 받은 구간은 메모리에 두고, 다음부터는 새로 생긴 구간만 받습니다."""
        today = now_kst().strftime("%Y%m%d")
        cached = self._today.get(code)
        if not cached or cached[0] != today:
            cached = (today, [])
        known = cached[1]
        # 마지막 봉은 아직 만들어지는 중일 수 있으므로 그 봉부터 다시 받습니다
        stop_at = known[-1]["time"] if known else None
        hour = now_kst().strftime("%H%M%S")
        out: list[dict] = []
        for _ in range(60):
            body = await self.rest.get(f"{QUOTE}/inquire-time-itemchartprice", "FHKST03010200", {
                "FID_COND_MRKT_DIV_CODE": self.s.market,
                "FID_INPUT_ISCD": code,
                "FID_INPUT_HOUR_1": hour,
                "FID_PW_DATA_INCU_YN": "N",
                "FID_ETC_CLS_CODE": "",
            })
            bars = [b for b in (self._minute_row(r) for r in body.get("output2") or []) if b]
            bars = [b for b in bars if b["time"] >= kst_epoch(today)]
            if not bars:
                break
            oldest = min(b["time"] for b in bars)
            if out and oldest >= min(x["time"] for x in out):
                break  # 더 과거로 진행되지 않음
            out.extend(bars)
            if stop_at is not None and oldest <= stop_at:
                break
            d, h = _minus_one_minute(*_parts(oldest))
            if d != today:
                break
            hour = h
        merged = dedupe_sort(known + out)
        self._today[code] = (today, merged)
        return [dict(b) for b in merged]

    async def _past_minutes(self, code: str, days: int) -> list[dict]:
        """오늘 이전 영업일의 1분봉을 days 일치 모읍니다. 다 받은 날짜는 디스크에 캐시합니다."""
        today = now_kst().strftime("%Y%m%d")
        done: dict[str, list[dict]] = {}     # 끝까지 받은 날짜 (휴장일 제외)
        partial: list[dict] = []             # 지금 받고 있는 날짜
        date = (now_kst() - timedelta(days=1)).strftime("%Y%m%d")
        hour = "200000"
        empty_days = 0
        calls = 0

        def prev_day(x: str) -> str:
            return (datetime.strptime(x, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")

        while len(done) < days and empty_days < 10 and calls < 400:
            if datetime.strptime(date, "%Y%m%d").weekday() >= 5:  # 토·일은 호출하지 않음
                date, hour = prev_day(date), "200000"
                continue
            cached = self._read_cache(code, date) if not partial else None
            if cached is not None:
                if cached:
                    done[date] = cached
                    empty_days = 0
                else:
                    empty_days += 1
                date, hour = prev_day(date), "200000"
                continue
            calls += 1
            body = await self.rest.get(f"{QUOTE}/inquire-time-dailychartprice", "FHKST03010230", {
                "FID_COND_MRKT_DIV_CODE": self.s.market,
                "FID_INPUT_ISCD": code,
                "FID_INPUT_HOUR_1": hour,
                "FID_INPUT_DATE_1": date,
                "FID_PW_DATA_INCU_YN": "N",
                "FID_FAKE_TICK_INCU_YN": "",
            })
            bars = [b for b in (self._minute_row(r) for r in body.get("output2") or []) if b]
            known = {b["time"] for b in partial}
            new = [b for b in bars if _parts(b["time"])[0] == date and b["time"] not in known]
            if new:
                partial = dedupe_sort(partial + new)
                d, h = _minus_one_minute(*_parts(partial[0]["time"]))
                if d == date and h >= "080000":
                    hour = h
                    continue
            # 이 날짜는 끝까지 받았거나 휴장일입니다 -> 캐시하고 전날로 이동
            if date != today:
                self._write_cache(code, date, partial)
            if partial:
                done[date] = partial
                empty_days = 0
            else:
                empty_days += 1
            partial = []
            date, hour = prev_day(date), "200000"
        out: list[dict] = []
        for d in sorted(done)[-days:]:
            out.extend(done[d])
        return out

    def _cache_path(self, code: str, date: str) -> Path:
        return self._cache_dir / f"{code}_{date}.json"

    def _read_cache(self, code: str, date: str) -> list[dict] | None:
        key = (code, date)
        if key in self._mem:
            return self._mem[key]
        p = self._cache_path(code, date)
        if p.exists():
            try:
                bars = json.loads(p.read_text(encoding="utf-8"))
                self._mem[key] = bars
                return bars
            except Exception:
                return None
        return None

    def _write_cache(self, code: str, date: str, bars: list[dict]) -> None:
        self._mem[(code, date)] = bars
        try:
            self._cache_path(code, date).write_text(json.dumps(bars), encoding="utf-8")
        except Exception as e:
            log.warning("분봉 캐시 저장 실패: %s", e)

    async def minute_bars(self, code: str, days: int) -> list[dict]:
        today = await self._today_minutes(code)
        past_days = days - (1 if today else 0)
        past = await self._past_minutes(code, past_days) if past_days > 0 else []
        return dedupe_sort(past + today)

    async def close(self) -> None:
        await self.hub.close()
        await self.rest.close()

