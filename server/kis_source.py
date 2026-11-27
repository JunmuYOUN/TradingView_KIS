"""한국투자증권 Open API 데이터 소스 (HTTPS 차트 및 시세 갱신).

참고: https://github.com/koreainvestment/open-trading-api (examples_llm/domestic_stock)
  - 국내주식기간별시세(일/주/월/년)  FHKST03010100  1회 최대 100건
  - 주식당일분봉조회                 FHKST03010200  1회 최대 30건, 당일만
  - 주식일별분봉조회                 FHKST03010230  1회 최대 120건, 최대 1년
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx

from . import vault
from .bars import aggregate, bucket_start, dedupe_sort, epoch_to_kst_parts as _parts, kst_epoch, now_kst
from .codes import index_kis_code, is_index, is_overseas, is_us_index, split_overseas
from .config import DATA_DIR, Settings

log = logging.getLogger("kis")

QUOTE = "/uapi/domestic-stock/v1/quotations"
OVERSEAS_QUOTE = "/uapi/overseas-price/v1/quotations"
OVERSEAS_MAX_PAGES = 12      # 해외 분봉 1회 조회당 최대 호출 수 (1페이지 120개)
PERIOD_GUBN = {"D": "0", "W": "1", "M": "2"}
HHMMSS_RE = re.compile(r"^([01]\d|2[0-3])[0-5]\d[0-5]\d$")
DATE_RE = re.compile(r"^[0-9]{8}$")


class KisError(RuntimeError):
    def __init__(self, code: str, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code


class KisCredentialError(KisError):
    """The token endpoint rejected the credentials in this environment."""


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


def _index_bar(row: dict, prefix: str, intraday: bool) -> dict | None:
    if not isinstance(row, dict):
        return None
    date = str(row.get("stck_bsop_date", ""))
    hour = str(row.get("stck_cntg_hour", "")) if intraday else "000000"
    if not re.fullmatch(r"[0-9]{8}", date) or not HHMMSS_RE.fullmatch(hour):
        return None
    try:
        # Reported trading date/time stays on the chart's wall-clock time axis.
        datetime.strptime(date, "%Y%m%d")
        stamp = kst_epoch(date, hour)
    except ValueError:
        return None
    o, h, lo, c = (_num(row.get(prefix + key)) for key in ("oprc", "hgpr", "lwpr", "prpr"))
    if min(o, h, lo, c) <= 0 or h < max(o, c) or lo > min(o, c):
        return None
    return {"time": stamp, "open": o, "high": h, "low": lo, "close": c,
            "volume": max(0.0, _num(row.get("cntg_vol" if intraday else "acml_vol")))}


def _stock_period_bar(row: dict, overseas: bool) -> dict | None:
    if not isinstance(row, dict):
        return None
    date = str(row.get("xymd" if overseas else "stck_bsop_date", ""))
    if not DATE_RE.fullmatch(date):
        return None
    try:
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        return None
    fields = ("open", "high", "low", "clos") if overseas else ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr")
    o, h, lo, c = (_num(row.get(key)) for key in fields)
    if c <= 0:
        return None
    return {"time": kst_epoch(date), "open": o or c, "high": h or c, "low": lo or c, "close": c,
            "volume": _num(row.get("tvol" if overseas else "acml_vol"))}


# ---------------------------------------------------------------- 인증
class KisAuth:
    """REST 접근토큰을 암호화 파일(Windows DPAPI)에 캐시해서 재사용합니다.

    접근토큰은 유효기간 1일, 발급은 1분에 1회로 제한되므로 매번 새로 받으면 안 됩니다.
    Windows 가 아니면 디스크에 남기지 않고 메모리에만 둡니다.
    """

    def __init__(self, s: Settings, client: httpx.AsyncClient) -> None:
        self.s = s
        self.client = client
        self._lock = asyncio.Lock()
        self.closed = False
        credentials = json.dumps([s.app_key, s.app_secret]).encode("utf-8")
        self._credential_id = sha256(credentials).hexdigest()
        self._token_file = DATA_DIR / f"kis_token_{s.env}.bin"
        self._token: str | None = None
        self._token_exp: float = 0.0

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
            if self.closed:
                raise KisError("SESSION", "종료된 연결입니다")
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
                code = _clean(body.get("error_code") or r.status_code, 20)
                rejected = r.status_code in (400, 401, 403) or (r.status_code == 500 and body.get("error_code"))
                if rejected and code not in ("EGW00133", "EGW00201"):
                    raise KisCredentialError(code, "인증키가 거절되었습니다.")
                raise KisError(code, "접근토큰 발급 실패")
            exp_str = body.get("access_token_token_expired")  # "2026-11-26 21:00:00" (KST)
            try:
                exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=now_kst().tzinfo).timestamp()
            except Exception:
                exp = now + int(body.get("expires_in", 86400))
            if self.closed:
                raise KisError("SESSION", "종료된 연결입니다")
            self._token, self._token_exp = body["access_token"], exp
            self._save(self._token_file, {"access_token": self._token, "expires_at": exp})
            log.info("KIS 접근토큰 발급 완료")
            return self._token

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
            code = _clean(body.get("msg_cd") or r.status_code, 20)
            if code == "EGW00201":            # 초당 거래건수 초과
                await asyncio.sleep(1.0 + attempt)
                continue
            if code in ("EGW00123", "EGW00121") and not retried_token:  # 토큰 만료/무효
                await self.auth.token(force=True)
                retried_token = True
                continue
            raise KisError(code, "한투 시세 조회가 거절되었습니다.")
        raise KisError("EGW00201", "초당 거래건수 초과가 반복되었습니다")

    async def domestic_period_page(self, code: str, tf: str, market: str, start: str, end: str) -> dict:
        return await self.get(f"{QUOTE}/inquire-daily-itemchartprice", "FHKST03010100", {
            "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": tf, "FID_ORG_ADJ_PRC": "0",  # 수정주가
        })

    async def verify_session(self) -> None:
        # 사용자가 선택한 거래소의 데이터 제공 여부와 무관하게 KRX에서 인증을 확인합니다.
        today = now_kst().strftime("%Y%m%d")
        await self.domestic_period_page("005930", "D", "J", today, today)

    async def close(self) -> None:
        self.auth.closed = True
        self.auth._token = None
        await self.client.aclose()


class KisSource:
    name = "kis"

    def __init__(self, s: Settings, *, rest: KisRest | None = None) -> None:
        self.s = s
        self._cache_dir = DATA_DIR / "cache" / s.market
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self.rest = rest if rest is not None else KisRest(s)
        self._mem: dict[tuple[str, str], list[dict]] = {}
        self._today: dict[str, tuple[str, list[dict]]] = {}
        self._us_index_minutes: dict[str, tuple[float, dict[int, dict]]] = {}

    # ---- 해외 (미국)
    @staticmethod
    def _overseas_minute_row(r: dict) -> dict | None:
        d, h = str(r.get("xymd", "")), str(r.get("xhms", ""))
        c = _num(r.get("last"))
        if not DATE_RE.match(d) or not HHMMSS_RE.match(h) or c <= 0:
            return None
        o, hi, lo = _num(r.get("open")), _num(r.get("high")), _num(r.get("low"))
        return {"time": kst_epoch(d, h), "open": o or c, "high": hi or c, "low": lo or c, "close": c,
                "volume": _num(r.get("evol"))}

    async def _overseas_minutes(self, code: str, days: int, tf: str, *, pages: int = OVERSEAS_MAX_PAGES,
                                before: int | None = None) -> list[dict]:
        """해외주식분봉조회 HHDFS76950200. tf 분봉을 KIS 에서 바로 받습니다 (현지 시각, 시간외 포함).

        다음 페이지는 NEXT=1, KEYB=직전 페이지의 가장 오래된 봉보다 1분 전의 현지 일시로 받습니다.
        """
        excd, symb = split_overseas(code)
        bars: dict[int, dict] = {}
        cursor = before
        for _ in range(pages):
            keyb = "".join(_parts((cursor - 1) // 60 * 60)) if cursor is not None else ""
            body = await self.rest.get(f"{OVERSEAS_QUOTE}/inquire-time-itemchartprice", "HHDFS76950200", {
                "AUTH": "", "EXCD": excd, "SYMB": symb, "NMIN": tf, "PINC": "1",  # 전일 포함
                "NEXT": "1" if keyb else "", "NREC": "120", "FILL": "", "KEYB": keyb,
            })
            page = [b for b in (self._overseas_minute_row(r) for r in body.get("output2") or []) if b]
            new = [b for b in page if cursor is None or b["time"] < cursor]
            if not new:
                break
            for b in new:
                bars[b["time"]] = b
            cursor = min(b["time"] for b in new)
            if len({_parts(t)[0] for t in bars}) > days:
                break  # 필요한 날짜보다 하루 더 받았으면 충분합니다
        out = dedupe_sort(list(bars.values()))
        keep = sorted({_parts(b["time"])[0] for b in out})[-days:]
        return [b for b in out if _parts(b["time"])[0] in keep]

    # ---- 일/주/월봉
    async def _period_page(self, code: str, tf: str, end: str) -> list[dict]:
        # 최근 조회에 쓰던 시작일을 유지하되 오래된 차트에선 조회 창도 함께 과거로 이동합니다.
        start = min("19700101", f"{int(end[:4]) - 10:04d}0101")
        if is_index(code):
            us = is_us_index(code)
            path, tr_id = ((f"{OVERSEAS_QUOTE}/inquire-daily-chartprice", "FHKST03030100") if us else
                           (f"{QUOTE}/inquire-daily-indexchartprice", "FHKUP03500100"))
            body = await self.rest.get(path, tr_id, {
                "FID_COND_MRKT_DIV_CODE": "N" if us else "U", "FID_INPUT_ISCD": index_kis_code(code),
                "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end, "FID_PERIOD_DIV_CODE": tf,
            })
            prefix = "ovrs_nmix_" if us else "bstp_nmix_"
            page = (_index_bar(row, prefix, False) for row in body.get("output2") or [])
            return [bar for bar in page if bar is not None]
        overseas = is_overseas(code)
        if overseas:
            excd, symb = split_overseas(code)
            body = await self.rest.get(f"{OVERSEAS_QUOTE}/dailyprice", "HHDFS76240000", {
                "AUTH": "", "EXCD": excd, "SYMB": symb, "GUBN": PERIOD_GUBN[tf],
                "BYMD": end, "MODP": "1", "KEYB": "",
            })
        else:
            body = await self.rest.domestic_period_page(code, tf, self.s.market, start, end)
        page = (_stock_period_bar(row, overseas) for row in body.get("output2") or [])
        return [bar for bar in page if bar is not None]

    async def period_bars(self, code: str, tf: str, count: int, *, before: int | None = None) -> list[dict]:
        """국내·미국 주식과 지수. before는 가장 오래된 봉의 차트 시각이며 그 봉은 제외합니다."""
        today = kst_epoch(now_kst().strftime("%Y%m%d"))
        end = today if before is None else min(today, bucket_start(before, tf) - 86400)
        bars: dict[int, dict] = {}
        for _ in range(64):
            page = await self._period_page(code, tf, _parts(end)[0])
            new = [b for b in page if b["time"] <= end]
            if not new:
                break
            for bar in new:
                start = bucket_start(bar["time"], tf)
                if is_index(code):
                    bar["time"] = start
                bars[start] = bar
            if len(bars) >= count:
                break
            end = min(bars) - 86400
        return [bars[t] for t in sorted(bars)[-count:]]

    # ---- 분봉
    async def _index_minutes(self, code: str, days: int, tf: str, *, latest: bool = False,
                             snapshot_only: bool = False) -> list[dict]:
        if self.s.env == "demo":
            raise KisError("INDEX_MINUTE", "한투 모의투자는 지수 분봉을 제공하지 않습니다. 일봉·주봉·월봉으로 조회하세요.")
        us = is_us_index(code)
        params = {"FID_COND_MRKT_DIV_CODE": "N" if us else "U", "FID_INPUT_ISCD": index_kis_code(code),
                  "FID_PW_DATA_INCU_YN": "Y"}
        if us:
            path, tr_id = f"{OVERSEAS_QUOTE}/inquire-time-indexchartprice", "FHKST03030200"
            params["FID_HOUR_CLS_CODE"] = "0"  # 정규장
        else:
            path, tr_id = f"{QUOTE}/inquire-time-indexchartprice", "FHKUP03500200"
            params.update(FID_ETC_CLS_CODE="1", FID_INPUT_HOUR_1=str(int(tf) * 60))
        requested_at = time.monotonic()
        body = await self.rest.get(path, tr_id, params)  # 두 TR 모두 연속조회 미지원, 최대 102봉
        page = [_index_bar(row, "optn_" if us else "bstp_nmix_", True) for row in body.get("output2") or []]
        bars = {b["time"]: b for b in page if b}
        if not bars:
            return []
        if us:
            # 미국 지수는 시간 간격 인자가 없으므로 수신된 1분봉을 보관해 더 큰 봉을 구성합니다.
            # 동시에 끝난 과거 응답이 새로 갱신한 마지막 봉을 덮어쓰지 않게 합니다.
            snapshot = bars
            previous_at, previous = self._us_index_minutes.pop(code, (0.0, {}))
            bars = previous | bars if requested_at >= previous_at else bars | previous
            bars = {t: bars[t] for t in sorted(bars)[-3000:]}
            if len(self._us_index_minutes) >= 16:
                self._us_index_minutes.pop(next(iter(self._us_index_minutes)))
            self._us_index_minutes[code] = (max(requested_at, previous_at), bars)
            if snapshot_only:
                bars = snapshot
        first_day = sorted({t // 86400 for t in bars})[-days:][0]
        ordered = [bars[t] for t in sorted(bars) if t // 86400 >= first_day]
        if not us or tf == "1":
            return ordered[-3:] if latest else ordered
        shift = 1800 if tf == "60" else 0  # 미국 정규장 60분봉은 09:30에 시작합니다.
        if shift:
            ordered = [dict(b, time=b["time"] - shift) for b in ordered]
        grouped = aggregate(ordered, tf)
        # 조회 한도 때문에 맨 앞 구간의 시가·고저를 알 수 없을 때는 다음 구간부터 표시합니다.
        if (snapshot_only or len(grouped) > 1) and grouped[0]["time"] < ordered[0]["time"]:
            grouped = grouped[1:]
        if shift:
            for b in grouped:
                b["time"] += shift
        return grouped[-3:] if latest else grouped

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

    async def _past_minutes(self, code: str, days: int, *, before: int | None = None) -> list[dict]:
        """오늘 이전 영업일의 1분봉을 days 일치 모읍니다. 다 받은 날짜는 디스크에 캐시합니다."""
        now = now_kst()
        retention_start = (now - timedelta(days=366)).strftime("%Y%m%d")
        done: dict[str, list[dict]] = {}     # 끝까지 받은 날짜 (휴장일 제외)
        partial: list[dict] = []             # 지금 받고 있는 날짜
        date = (now - timedelta(days=1)).strftime("%Y%m%d")
        if before is not None:
            date = min(date, _parts(before - 1)[0])
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
                eligible = cached if before is None else [b for b in cached if b["time"] < before]
                if eligible:
                    done[date] = eligible
                    empty_days = 0
                else:
                    empty_days += 1
                date, hour = prev_day(date), "200000"
                continue
            if date < retention_start:
                break  # 한투가 보관하는 범위 밖에서는 이전에 저장한 분봉만 사용합니다.
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
            self._write_cache(code, date, partial)
            eligible = partial if before is None else [b for b in partial if b["time"] < before]
            if eligible:
                done[date] = eligible
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

    async def recent_bars(self, code: str, tf: str) -> list[dict]:
        if tf in PERIOD_GUBN:
            return await self.period_bars(code, tf, 3)
        if is_index(code):
            return await self._index_minutes(code, 2, tf, latest=True)
        if is_overseas(code):
            return await self._overseas_minutes(code, 2, tf, pages=1)
        minutes = await self._today_minutes(code)
        return minutes if tf == "1" else aggregate(minutes, tf)

    async def index_recovery_bars(self, code: str, tf: str) -> list[dict]:
        return await self._index_minutes(code, 60, tf, snapshot_only=True)

    async def minute_bars(self, code: str, days: int, tf: str = "1", *, before: int | None = None) -> list[dict]:
        if is_index(code):
            # 지수 분봉 TR에는 이전 응답의 날짜·시각을 지정하는 요청 필드가 없습니다.
            if before is not None:
                return []
            return await self._index_minutes(code, days, tf)
        if is_overseas(code):
            return await self._overseas_minutes(code, days, tf, before=before)
        today = []
        if before is None or before > kst_epoch(now_kst().strftime("%Y%m%d")):
            today = await self._today_minutes(code)
            if before is not None:
                today = [b for b in today if b["time"] < before]
        past_days = days - (1 if today else 0)
        past = []
        if past_days > 0 and self.s.env != "demo":
            past = await self._past_minutes(code, past_days, before=before)
        bars = dedupe_sort(past + today)
        return bars if tf == "1" else aggregate(bars, tf)

    async def close(self) -> None:
        await self.rest.close()

