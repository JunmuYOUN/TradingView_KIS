import { TIMEFRAMES, bucketStart, isMinuteTf, tfSeconds } from './timeframe.js';
import { TimeMap } from './timemap.js';
import { DrawingManager, TOOLS } from './drawings.js';

const LC = window.LightweightCharts;

// 자주 보는 종목 (이름으로 입력하면 코드로 바꿔 줍니다). 필요하면 자유롭게 추가하세요.
const SYMBOLS = {
  삼성전자: '005930', SK하이닉스: '000660', LG에너지솔루션: '373220', 삼성바이오로직스: '207940',
  현대차: '005380', 기아: '000270', 셀트리온: '068270', NAVER: '035420', 카카오: '035720',
  POSCO홀딩스: '005490', KB금융: '105560', 삼성SDI: '006400', LG화학: '051910', 한화에어로스페이스: '012450',
  KODEX200: '069500', 'KODEX 레버리지': '122630',
};

const UP = '#ef5350';   // 국내 관례: 상승 빨강
const DOWN = '#2962ff'; // 하락 파랑

const $ = (s) => document.querySelector(s);
const state = {
  code: localStorage.getItem('kis.code') || '005930',
  tf: localStorage.getItem('kis.tf') || 'D',
  bars: [],
  ws: null,
  wsRetry: null,
  loadSeq: 0,
  saveTimer: null,
};

// ------------------------------------------------------------ 차트
const container = $('#chart');
const chart = LC.createChart(container, {
  autoSize: true,
  layout: { background: { color: '#131722' }, textColor: '#d1d4dc', fontFamily: '-apple-system, "Segoe UI", "Malgun Gothic", sans-serif' },
  grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
  crosshair: { mode: LC.CrosshairMode.Normal },
  rightPriceScale: { borderColor: '#2a2e39' },
  timeScale: { borderColor: '#2a2e39', timeVisible: true, secondsVisible: false, rightOffset: 12 },
  localization: { locale: 'ko-KR', priceFormatter: (p) => Math.round(p).toLocaleString('ko-KR') },
});

const candles = chart.addSeries(LC.CandlestickSeries, {
  upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN,
  priceFormat: { type: 'price', precision: 0, minMove: 1 },
});
candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });

const volume = chart.addSeries(LC.HistogramSeries, {
  priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false,
});
volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

const timeMap = new TimeMap();
const drawings = new DrawingManager({
  chart, series: candles, container, timeMap, getBars: () => state.bars,
});

const volBar = (b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(239,83,80,.45)' : 'rgba(41,98,255,.45)' });

// ------------------------------------------------------------ 데이터 로드
async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

async function loadCandles({ keepRange = false } = {}) {
  const seq = ++state.loadSeq;
  const range = keepRange ? chart.timeScale().getVisibleLogicalRange() : null;
  setStatus('loading', '차트 불러오는 중…');
  try {
    const { bars } = await api(`/api/candles?code=${state.code}&tf=${state.tf}`);
    if (seq !== state.loadSeq) return;
    state.bars = bars;
    timeMap.setBars(bars, tfSeconds(state.tf), state.tf === 'D');
    candles.setData(bars.map((b) => ({ ...b }))); // 라이브러리가 객체를 건드리지 않도록 복사본 전달
    volume.setData(bars.map(volBar));
    if (range) chart.timeScale().setVisibleLogicalRange(range);
    else if (!keepRange) chart.timeScale().setVisibleLogicalRange({ from: bars.length - 150, to: bars.length + 10 });
    drawings.update();
    updateLegend();
    setStatus('ok', `${bars.length}개 봉`);
  } catch (e) {
    if (seq === state.loadSeq) setStatus('error', `불러오기 실패: ${e.message}`);
  }
}

async function loadDrawings() {
  try {
    const { drawings: list } = await api(`/api/drawings/${state.code}`);
    drawings.load(list);
  } catch (e) {
    drawings.load([]);
  }
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  const code = state.code;
  const body = JSON.stringify({ drawings: drawings.toJSON() });
  state.saveTimer = setTimeout(async () => {
    try {
      await api(`/api/drawings/${code}`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
      $('#saved').textContent = '저장됨';
    } catch (e) {
      $('#saved').textContent = '저장 실패';
    }
  }, 400);
  $('#saved').textContent = '저장 중…';
}
drawings.onChange = scheduleSave;

// ------------------------------------------------------------ 실시간
function applyTick({ t, p, v }) {
  const bars = state.bars;
  if (!bars.length) return;
  const last = bars[bars.length - 1];
  const bNew = bucketStart(t, state.tf);
  const bLast = bucketStart(last.time, state.tf);
  let bar;
  if (bNew === bLast) {
    last.high = Math.max(last.high, p);
    last.low = Math.min(last.low, p);
    last.close = p;
    last.volume += v;
    bar = last;
  } else if (bNew > bLast) {
    bar = { time: bNew, open: p, high: p, low: p, close: p, volume: v };
    bars.push(bar);
    timeMap.push(bNew);
  } else {
    return; // 이미 지난 봉의 틱
  }
  candles.update({ ...bar });
  volume.update(volBar(bar));
  updateLegend();
}

function connectWs() {
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
  }
  clearTimeout(state.wsRetry);
  const code = state.code;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/ticks?code=${code}`);
  state.ws = ws;
  let opened = false;
  ws.onopen = () => {
    opened = true;
    setLive('connecting', '실시간 연결 중');
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'tick') {
      applyTick(msg);
      setLive('live', '실시간');
    } else if (msg.type === 'status') {
      setLive(msg.state, msg.msg);
    } else if (msg.type === 'resync') {
      loadCandles({ keepRange: true });
    }
  };
  ws.onclose = () => {
    setLive('off', '실시간 끊김, 다시 연결합니다');
    state.wsRetry = setTimeout(() => {
      if (state.code !== code) return;
      connectWs();
      if (opened) loadCandles({ keepRange: true }); // 끊긴 사이의 봉을 다시 받습니다
    }, 2000);
  };
}

// ------------------------------------------------------------ 화면 요소
function setStatus(kind, text) {
  const el = $('#status');
  el.textContent = text;
  el.dataset.kind = kind;
}

function setLive(kind, text) {
  const el = $('#live');
  el.dataset.kind = kind;
  el.title = text || '';
  $('#live-text').textContent = kind === 'live' ? 'LIVE' : kind === 'error' ? '오류' : kind === 'off' ? '끊김' : '연결 중';
  if (kind === 'error') setStatus('error', text);
}

function updateLegend(bar) {
  const b = bar || state.bars[state.bars.length - 1];
  if (!b) return;
  const i = state.bars.indexOf(b);
  const prev = i > 0 ? state.bars[i - 1] : null;
  const chg = prev ? b.close - prev.close : 0;
  const pct = prev ? (chg / prev.close) * 100 : 0;
  const f = (x) => Math.round(x).toLocaleString('ko-KR');
  const cls = chg > 0 ? 'up' : chg < 0 ? 'down' : '';
  $('#legend').innerHTML =
    `<span>시 <b>${f(b.open)}</b></span><span>고 <b>${f(b.high)}</b></span>` +
    `<span>저 <b>${f(b.low)}</b></span><span>종 <b class="${cls}">${f(b.close)}</b></span>` +
    `<span class="${cls}">${chg >= 0 ? '+' : ''}${f(chg)} (${pct.toFixed(2)}%)</span>` +
    `<span>거래량 <b>${f(b.volume)}</b></span>`;
}

chart.subscribeCrosshairMove((param) => {
  if (!param || param.time === undefined) return updateLegend();
  const bar = state.bars.find((x) => x.time === param.time);
  updateLegend(bar);
});

function buildToolbar() {
  const tfBox = $('#tfs');
  for (const t of TIMEFRAMES) {
    const b = document.createElement('button');
    b.textContent = t.label;
    b.dataset.tf = t.id;
    b.onclick = () => switchTf(t.id);
    tfBox.appendChild(b);
  }
  const toolBox = $('#tools');
  for (const [id, t] of Object.entries(TOOLS)) {
    const b = document.createElement('button');
    b.textContent = t.label;
    b.dataset.tool = id;
    b.title = `${t.label} (단축키 ${t.key.toUpperCase()})`;
    b.onclick = () => drawings.setTool(id);
    toolBox.appendChild(b);
  }
  const dl = $('#symbols');
  for (const [name, code] of Object.entries(SYMBOLS)) {
    const o = document.createElement('option');
    o.value = `${name} ${code}`;
    dl.appendChild(o);
  }

  $('#magnet').checked = drawings.magnet;
  $('#magnet').onchange = (e) => drawings.setMagnet(e.target.checked);
  $('#undo').onclick = () => drawings.undo();
  $('#clear').onclick = () => {
    if (confirm('이 종목에 그린 선을 모두 지울까요?')) drawings.clearAll();
  };
  $('#del').onclick = () => drawings.deleteSelected();
  $('#color').oninput = (e) => drawings.setSelectedStyle({ color: e.target.value });
  $('#width').onchange = (e) => drawings.setSelectedStyle({ width: Number(e.target.value) });
  $('#extend').onchange = (e) => drawings.setSelectedStyle({ extend: e.target.checked });

  $('#symbol').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      switchCode(e.target.value);
      e.target.blur();
    }
  });
  $('#symbol').addEventListener('change', (e) => switchCode(e.target.value));

  drawings.onToolChange = (tool) => {
    document.querySelectorAll('#tools button').forEach((b) => b.classList.toggle('on', b.dataset.tool === tool));
  };
  drawings.onSelect = (d) => {
    $('#style').hidden = !d;
    if (!d) return;
    $('#color').value = (d.style && d.style.color) || TOOLS[d.type].color;
    $('#width').value = String((d.style && d.style.width) || 1.5);
    $('#extend-wrap').hidden = d.type !== 'fib';
    $('#extend').checked = !!(d.style && d.style.extend);
  };
  drawings.onToolChange('cursor');
  drawings.onSelect(null);
}

function parseCode(text) {
  const s = String(text || '').trim();
  const m = s.match(/\b([0-9A-Z]{6})\b/i);
  if (m) return m[1].toUpperCase();
  const hit = Object.entries(SYMBOLS).find(([name]) => name.toLowerCase() === s.toLowerCase());
  return hit ? hit[1] : null;
}

function nameOf(code) {
  const hit = Object.entries(SYMBOLS).find(([, c]) => c === code);
  return hit ? hit[0] : '';
}

async function switchCode(text) {
  const code = parseCode(text);
  if (!code) {
    setStatus('error', '종목코드 6자리 또는 목록의 종목명을 입력하세요');
    return;
  }
  state.code = code;
  localStorage.setItem('kis.code', code);
  $('#symbol').value = `${nameOf(code)} ${code}`.trim();
  document.title = `${nameOf(code) || code} · KIS Tradingview`;
  await Promise.all([loadCandles(), loadDrawings()]);
  connectWs();
}

async function switchTf(tf) {
  state.tf = tf;
  localStorage.setItem('kis.tf', tf);
  document.querySelectorAll('#tfs button').forEach((b) => b.classList.toggle('on', b.dataset.tf === tf));
  chart.applyOptions({ timeScale: { timeVisible: isMinuteTf(tf) } });
  await loadCandles();
}

async function init() {
  buildToolbar();
  try {
    const cfg = await api('/api/config');
    const badge = $('#source');
    badge.textContent = cfg.source === 'kis' ? `KIS ${cfg.env === 'real' ? '실전' : '모의'} · ${cfg.market}` : 'MOCK 데이터';
    badge.dataset.kind = cfg.source;
  } catch (e) { /* 서버 미실행 */ }
  document.querySelectorAll('#tfs button').forEach((b) => b.classList.toggle('on', b.dataset.tf === state.tf));
  chart.applyOptions({ timeScale: { timeVisible: isMinuteTf(state.tf) } });
  await switchCode(state.code);
}

window.__app = { state, chart, candles, drawings, timeMap }; // 디버깅용
init();
