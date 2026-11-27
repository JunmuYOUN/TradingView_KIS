"""Yahoo Finance에서 공개하는 선물·환율·지수. 목록은 한투 마스터와 별도로 유지합니다.

https://finance.yahoo.com/markets/commodities/
https://finance.yahoo.com/markets/currencies/
https://finance.yahoo.com/quote/%5EVIX/
https://finance.yahoo.com/quote/DX-Y.NYB/
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class YahooInstrument:
    ticker: str
    name: str
    market: str
    kind: str
    english: str
    precision: int
    aliases: tuple[str, ...]


def _future(ticker: str, name: str, market: str, english: str, precision: int, *aliases: str) -> YahooInstrument:
    root = ticker.split("=")[0]
    return YahooInstrument(ticker, name, market, "선물", english, precision,
                           ("futures", "해외선물", english.removesuffix(" Futures"), root, root + "1!", *aliases))


def _fx(ticker: str, pair: str, name: str, *aliases: str) -> YahooInstrument:
    precision = 4 if pair in {"USD/KRW", "EUR/KRW", "USD/JPY"} else 5
    return YahooInstrument(ticker, name, "FX", "환율", pair, precision,
                           ("외환", "FX", "forex", "currency", pair.replace("/", ""), *aliases))


_INSTRUMENTS = (
    _future("ES=F", "S&P 500 선물", "CME", "E-mini S&P 500 Futures", 2, "에스앤피500 선물", "SP500 선물", "S&P 선물"),
    _future("NQ=F", "나스닥 100 선물", "CME", "E-mini Nasdaq 100 Futures", 2, "나스닥 선물", "나스닥100 선물"),
    _future("YM=F", "다우존스 선물", "CBOT", "E-mini Dow Jones Industrial Average Futures", 0, "다우 선물", "다우30 선물"),
    _future("RTY=F", "러셀 2000 선물", "CME", "E-mini Russell 2000 Futures", 1, "러셀 선물", "러셀2000"),
    _future("ZN=F", "미국 10년 국채 선물", "CBOT", "10-Year Treasury Note Futures", 6, "미국채 10년 선물", "10년물 국채 선물"),
    _future("CL=F", "WTI 원유 선물", "NYMEX", "West Texas Intermediate Crude Oil Futures", 2, "서부텍사스유", "원유", "유가", "WTI"),
    _future("BZ=F", "브렌트유 선물", "NYMEX", "Brent Crude Oil Last Day Financial Futures", 2, "브렌트 원유", "브렌트"),
    _future("NG=F", "천연가스 선물", "NYMEX", "Henry Hub Natural Gas Futures", 3, "천연가스", "헨리허브"),
    _future("GC=F", "금 선물", "COMEX", "Gold Futures", 2, "금", "골드", "국제 금값"),
    _future("SI=F", "은 선물", "COMEX", "Silver Futures", 3, "은", "실버", "은값"),
    _future("HG=F", "구리 선물", "COMEX", "Copper Futures", 4, "구리", "동 선물"),
    _future("ZC=F", "옥수수 선물", "CBOT", "Corn Futures", 2, "옥수수"),
    _future("ZS=F", "대두 선물", "CBOT", "Soybean Futures", 2, "대두", "콩 선물"),
    _future("ZW=F", "밀 선물", "CBOT", "Chicago SRW Wheat Futures", 2, "밀", "소맥"),
    _fx("KRW=X", "USD/KRW", "미국 달러 / 원", "달러원", "원달러", "원/달러", "원달러 환율", "원/달러 환율",
        "달러원 환율", "달러 환율", "원화", "US Dollar Korean Won"),
    _fx("JPY=X", "USD/JPY", "미국 달러 / 일본 엔", "달러엔", "달러/엔", "USDJPY=X", "US Dollar Japanese Yen"),
    _fx("EURUSD=X", "EUR/USD", "유로 / 미국 달러", "유로달러", "유로/달러", "Euro US Dollar"),
    _fx("GBPUSD=X", "GBP/USD", "영국 파운드 / 미국 달러", "파운드달러", "파운드/달러", "British Pound US Dollar"),
    _fx("AUDUSD=X", "AUD/USD", "호주 달러 / 미국 달러", "호주달러", "호주달러 환율", "Australian Dollar US Dollar"),
    _fx("CAD=X", "USD/CAD", "미국 달러 / 캐나다 달러", "미달러 캐나다달러", "US Dollar Canadian Dollar"),
    _fx("CHF=X", "USD/CHF", "미국 달러 / 스위스 프랑", "달러프랑", "US Dollar Swiss Franc"),
    _fx("CNY=X", "USD/CNY", "미국 달러 / 중국 위안", "달러위안", "달러/위안", "US Dollar Chinese Yuan"),
    _fx("EURKRW=X", "EUR/KRW", "유로 / 원", "유로원", "유로/원", "유로 환율", "Euro Korean Won"),
    _fx("JPYKRW=X", "JPY/KRW", "일본 엔 / 원", "엔원", "엔/원", "엔화 환율", "Japanese Yen Korean Won"),
    YahooInstrument("^GSPC", "S&P 500", "S&P DJI", "지수", "S&P 500 Index", 2,
                    ("SP500", "에스앤피500", "미국지수", "S&P 500 지수", "GSPC")),
    YahooInstrument("^IXIC", "나스닥 종합", "NASDAQ", "지수", "Nasdaq Composite", 2,
                    ("나스닥", "나스닥 지수", "나스닥 종합지수", "미국지수", "IXIC")),
    YahooInstrument("^KS11", "코스피", "KRX", "지수", "KOSPI Composite Index", 2,
                    ("코스피 지수", "코스피 종합", "종합주가지수", "국내지수", "KS11")),
    YahooInstrument("^VIX", "VIX 변동성 지수", "Cboe", "지수", "Cboe Volatility Index", 2,
                    ("VIX", "VIX 지수", "VIX 현물", "VIX spot", "미국지수", "index", "빅스", "빅스 지수", "공포지수",
                     "변동성", "미국 변동성 지수", "Fear Index", "Yahoo VIX", "야후 VIX")),
    YahooInstrument("DX-Y.NYB", "미국 달러 지수", "ICE", "지수", "US Dollar Index", 3,
                    ("DXY", "USDX", "미국지수", "index", "달러 인덱스", "달러지수", "미 달러화 지수")),
)

YAHOO_SYMBOLS = {"YF:" + instrument.ticker: instrument for instrument in _INSTRUMENTS}
