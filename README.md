# KIS Tradingview

한국투자증권(KIS) Open API 시세로 차트를 띄우고, TradingView처럼 차트 위에 선을 긋는 개인용 웹 도구입니다.

- 차트: [Lightweight Charts](https://github.com/tradingview/lightweight-charts) v5 (Apache-2.0, `web/vendor`에 포함)
- 서버: Python FastAPI (KIS 키를 브라우저에 노출하지 않기 위해 서버를 거칩니다)
- KIS 키가 없으면 **MOCK(가짜 시세)** 로 실행되므로 바로 화면을 확인할 수 있습니다.

## 실행

### Windows

`run.bat`을 더블클릭합니다. 처음 실행할 때 `.venv` 가상환경을 만들고 패키지를 설치하며, 이후에는 바로 서버가 켜지고 브라우저가 열립니다.

### macOS / Linux

```bash
./run.sh
```

### 직접 실행

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m server.main           # http://127.0.0.1:8000
```

## KIS 키 연결

1. [KIS Developers](https://apiportal.koreainvestment.com)에서 API를 신청하고 App Key와 App Secret을 발급받습니다. 모의투자 키로도 시세를 받을 수 있습니다.
2. `.env.example`을 `.env`로 복사하고 값을 채웁니다.

   ```
   KIS_APP_KEY=발급받은키
   KIS_APP_SECRET=발급받은시크릿
   KIS_ENV=demo        # 실전 키라면 real
   KIS_MARKET=J        # KRX만: J / KRX+NXT 통합: UN
   ```
3. 서버를 다시 실행합니다. 상단 배지가 `MOCK 데이터`에서 `KIS 모의 · J`로 바뀌면 연결된 것입니다.

`.env`와 `data/` 폴더(토큰 캐시, 분봉 캐시, 그린 선)는 `.gitignore`에 들어 있어서 git에 올라가지 않습니다.

## 사용법

| 도구 | 단축키 | 점 개수 |
|---|---|---|
| 선택 / 이동 | V | |
| 추세선 | T | 2 |
| 연장선(오른쪽으로 무한) | R | 2 |
| 수평선 | H | 1 |
| 피보나치 되돌림 | F | 2 (시작점 = 1, 끝점 = 0) |
| 평행 채널 | C | 3 (기준선 2점 + 폭 1점) |
| 엘리엇 충격파 | E | 6 (0, 1, 2, 3, 4, 5) |
| 엘리엇 조정파 | A | 4 (0, A, B, C) |

- 도구를 고른 뒤 차트를 클릭해 점을 찍습니다. 점을 다 찍으면 자동으로 선택 도구로 돌아갑니다.
- 선을 클릭하면 선택되고, 끌면 통째로 이동합니다. 선택한 선의 동그란 점을 끌면 그 점만 움직입니다.
- `Delete` 삭제, `Ctrl+Z` 되돌리기, `Esc` 그리기 취소입니다.
- **자석**을 켜 두면 점이 가장 가까운 시가·고가·저가·종가에 붙습니다.
- 충격파는 기본 규칙(2파가 1파 시작점을 넘지 않음, 3파가 가장 짧지 않음, 4파가 1파 영역과 겹치지 않음)을 검사해서 어긋나면 경고를 표시합니다.
- 선은 (시각, 가격)으로 저장되므로 타임프레임을 바꿔도 같은 자리에 남습니다. 종목별로 `data/drawings/<종목코드>.json`에 자동 저장됩니다.

## 다른 컴퓨터에서 쓰기

```bash
git clone <이 저장소 주소> KIS_Tradingview
cd KIS_Tradingview
copy .env.example .env      # 키 입력 (macOS/Linux: cp)
run.bat                     # macOS/Linux: ./run.sh
```

그린 선까지 컴퓨터 사이에서 공유하고 싶다면 `.gitignore`에 `!data/drawings/` 한 줄을 추가하고 커밋하면 됩니다.

## 구조

```
server/
  main.py          FastAPI 앱: /api/candles, /api/drawings, /ws/ticks, 정적 파일
  kis_source.py    KIS REST(일·주·월봉, 당일·과거 분봉) + WebSocket 실시간 체결
  mock_source.py   키 없이 확인하기 위한 가짜 시세 (24시간 생성)
  bars.py          KST 시간 처리, 타임프레임 집계
  hub.py           실시간 틱을 여러 브라우저 연결에 나눠 주는 허브
  config.py        .env 설정
web/
  index.html, css/style.css
  js/app.js        차트, 데이터 로드, 실시간 갱신, 툴바
  js/drawings.js   선긋기 도구 (Lightweight Charts series primitive)
  js/timemap.js    시각 <-> 봉 번호 변환 (미래 영역 포함)
  js/timeframe.js  타임프레임 규칙
```

### 사용하는 KIS API

| 용도 | TR | 1회 최대 |
|---|---|---|
| 일·주·월봉 | FHKST03010100 `inquire-daily-itemchartprice` | 100건 |
| 당일 분봉 | FHKST03010200 `inquire-time-itemchartprice` | 30건 |
| 과거 분봉 (최대 1년) | FHKST03010230 `inquire-time-dailychartprice` | 120건 |
| 실시간 체결 | H0STCNT0 / H0UNCNT0 / H0NXCNT0 (WebSocket) | 세션당 약 40종목 |

- 접근토큰은 하루 동안 유효하고 1분에 1회만 발급되므로 `data/kis_token_*.json`에 캐시합니다.
- 초당 호출 제한(실전 20건, 모의 5건)을 넘지 않도록 서버가 호출 간격을 조절합니다.
- 지난 날짜의 분봉은 `data/cache/`에 저장해 두므로, 같은 종목은 두 번째부터 빠르게 열립니다.
- 3·5·15·30·60분봉은 1분봉을 서버에서 묶어서 만듭니다. 분봉의 기본 조회 기간은 `server/main.py`의 `DEFAULT_DAYS`에서 바꿀 수 있습니다.

## 알려진 제한

- KIS 연동 코드는 공식 예제(koreainvestment/open-trading-api)의 파라미터를 기준으로 작성했고, KIS 응답 형식을 흉내 낸 가짜 서버로 검증했습니다. 실제 키로는 아직 확인하지 않았으므로 첫 실행 때 서버 로그를 확인해 주세요.
- 미래 영역에 찍은 분봉 점은 장 마감과 휴장 시간을 고려하지 않고 연속 시간으로 계산합니다. 일봉은 주말을 건너뜁니다.
- 지표(이동평균, RSI 등)와 종목 검색(종목 마스터 파일)은 아직 없습니다.
