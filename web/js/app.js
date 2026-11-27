import { TIMEFRAMES, bucketStart, isMinuteTf, tfSeconds } from './timeframe.js';
import { TimeMap } from './timemap.js';
import { DrawingManager, TOOLS, setPriceDecimals } from './drawings.js';

const LC = window.LightweightCharts;

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

// 종목 코드: 국내 6자리(005930), 해외 거래소:티커(NAS:AAPL)
const DOMESTIC_RE = /^(?=.*\d)[0-9A-Z]{6}$/;
const OVERSEAS_RE = /^(NAS|NYS|AMS):[A-Z0-9][A-Z0-9.-]{0,15}$/;
const isCode = (c) => DOMESTIC_RE.test(c) || OVERSEAS_RE.test(c);
const isOverseas = (code) => code.includes(':');
const tickerOf = (code) => (isOverseas(code) ? code.split(':')[1] : code);

// 가격 표시: 국내는 원 단위, 해외는 달러 소수 2자리 (1달러 미만은 4자리)
let priceDecimals = 0;
const fmtPrice = (p) => p.toLocaleString('ko-KR', { minimumFractionDigits: priceDecimals, maximumFractionDigits: priceDecimals });
const fmtInt = (x) => Math.round(x).toLocaleString('ko-KR');

// ------------------------------------------------------------ 차트
const container = $('#chart');
const chart = LC.createChart(container, {
  autoSize: true,
  layout: { background: { color: '#131722' }, textColor: '#d1d4dc', fontFamily: '-apple-system, "Segoe UI", "Malgun Gothic", sans-serif' },
  grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
  crosshair: { mode: LC.CrosshairMode.Normal },
  rightPriceScale: { borderColor: '#2a2e39' },
  timeScale: { borderColor: '#2a2e39', timeVisible: true, secondsVisible: false, rightOffset: 12 },
  localization: { locale: 'ko-KR', priceFormatter: (p) => fmtPrice(p) },
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

function applyPriceFormat(code, bars) {
  const last = bars.length ? bars[bars.length - 1].close : 0;
  const d = isOverseas(code) ? (last > 0 && last < 1 ? 4 : 2) : 0;
  if (d === priceDecimals) return;
  priceDecimals = d;
  setPriceDecimals(d);
  candles.applyOptions({ priceFormat: { type: 'price', precision: d, minMove: 1 / 10 ** d } });
}

async function loadCandles({ keepRange = false } = {}) {
  const seq = ++state.loadSeq;
  const range = keepRange ? chart.timeScale().getVisibleLogicalRange() : null;
  setStatus('loading', '차트 불러오는 중…');
  try {
    const { bars } = await api(`/api/candles?code=${encodeURIComponent(state.code)}&tf=${state.tf}`);
    if (seq !== state.loadSeq) return;
    applyPriceFormat(state.code, bars);
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
    const { drawings: list } = await api(`/api/drawings/${encodeURIComponent(state.code)}`);
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
      await api(`/api/drawings/${encodeURIComponent(code)}`, { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
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
  const ws = new WebSocket(`${proto}://${location.host}/ws/ticks?code=${encodeURIComponent(code)}`);
  state.ws = ws;
  let opened = false;
  ws.onopen = () => {
    opened = true;
    setLive('connecting', '실시간 연결 중');
  };
  ws.onmessage = (ev) => {
    // 종목을 바꾼 뒤 새 차트를 불러오는 동안에도 이전 종목의 연결이 잠시 남아 있습니다.
    // 그 틱이 새 종목의 봉에 섞이지 않게 버립니다.
    if (state.code !== code) return;
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
  const f = fmtPrice;
  const cls = chg > 0 ? 'up' : chg < 0 ? 'down' : '';
  $('#legend').innerHTML =
    `<span>시 <b>${f(b.open)}</b></span><span>고 <b>${f(b.high)}</b></span>` +
    `<span>저 <b>${f(b.low)}</b></span><span>종 <b class="${cls}">${f(b.close)}</b></span>` +
    `<span class="${cls}">${chg >= 0 ? '+' : ''}${f(chg)} (${pct.toFixed(2)}%)</span>` +
    `<span>거래량 <b>${fmtInt(b.volume)}</b></span>`;
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

  setupSearch();

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

// ------------------------------------------------------------ 종목 검색 · 검색 기록
const names = new Map(); // 종목코드 -> 종목명 (검색 결과·기록·서버 조회에서 알게 된 이름)
const symbolInput = $('#symbol');
const suggest = $('#suggest');
const search = { mode: 'closed', list: [], active: -1, query: '', emptyText: '', seq: 0, timer: null, history: [] };

// "삼성전자 005930", "005930", "NAS:AAPL" 에서 코드를 꺼냅니다 (KRX 코드에는 숫자가 들어 있습니다).
// 거래소 없이 쓴 티커(AAPL)는 검색 결과에서 고릅니다.
function parseCode(text) {
  const parts = String(text || '').trim().toUpperCase().split(/\s+/);
  const last = parts[parts.length - 1] || '';
  return isCode(last) ? last : null;
}

function symbolLabel(code) {
  return `${names.get(code) || ''} ${tickerOf(code)}`.trim();
}

function showSymbol(code) {
  if (document.activeElement !== symbolInput) symbolInput.value = symbolLabel(code);
  document.title = `${names.get(code) || tickerOf(code)} · KIS Tradingview`;
}

function learnNames(items) {
  for (const it of items) if (it.name) names.set(it.code, it.name);
}

async function lookupName(code) {
  try {
    const s = await api(`/api/symbols/${encodeURIComponent(code)}`);
    if (s.name) names.set(code, s.name);
    if (state.code === code) showSymbol(code);
  } catch (e) { /* 이름을 몰라도 코드로 볼 수 있습니다 */ }
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// 종목명은 외부 파일에서 온 값이므로 textContent 로만 넣습니다. 검색어와 같은 부분은 강조합니다.
function nameCell(name, query) {
  const span = el('span', 'sg-name');
  const q = query.trim().toLowerCase();
  const i = q ? name.toLowerCase().indexOf(q) : -1;
  if (i < 0) span.textContent = name;
  else span.append(name.slice(0, i), el('mark', '', name.slice(i, i + q.length)), name.slice(i + q.length));
  return span;
}

function closeSuggest() {
  search.mode = 'closed';
  search.list = [];
  search.active = -1;
  suggest.hidden = true;
  suggest.replaceChildren();
  symbolInput.setAttribute('aria-expanded', 'false');
}

function markActive(scroll) {
  suggest.querySelectorAll('.sg-item').forEach((row, i) => {
    const on = i === search.active;
    row.classList.toggle('on', on);
    row.setAttribute('aria-selected', String(on));
    if (on && scroll) row.scrollIntoView({ block: 'nearest' });
  });
}

function renderSuggest() {
  const isHistory = search.mode === 'history';
  suggest.replaceChildren();
  if (isHistory) {
    const head = el('div', 'sg-head');
    head.append(el('span', '', '최근 검색'));
    if (search.list.length) {
      const clear = el('button', 'sg-clear', '전체 삭제');
      clear.type = 'button';
      clear.onclick = clearHistory;
      head.append(clear);
    }
    suggest.append(head);
  }
  if (!search.list.length) {
    suggest.append(el('div', 'sg-empty', isHistory ? '검색 기록이 없습니다. 종목명 일부나 코드를 입력하세요.' : search.emptyText));
  }
  search.list.forEach((item, i) => {
    const row = el('div', 'sg-item');
    row.setAttribute('role', 'option');
    row.append(nameCell(item.name || '이름 모름', isHistory ? '' : search.query),
      el('span', 'sg-code', item.symbol || tickerOf(item.code)),
      el('span', 'sg-kind', item.kind && item.kind !== '주식' ? item.kind : item.market || ''));
    if (isHistory) {
      const del = el('button', 'sg-del', '×');
      del.type = 'button';
      del.title = '검색 기록에서 지우기';
      del.onclick = (e) => {
        e.stopPropagation();
        removeHistory(item.code);
      };
      row.append(del);
    }
    row.onclick = () => choose(item);
    row.onmousemove = () => {
      if (search.active !== i) {
        search.active = i;
        markActive(false);
      }
    };
    suggest.append(row);
  });
  markActive(true);
  suggest.hidden = false;
  symbolInput.setAttribute('aria-expanded', 'true');
}

async function openHistory() {
  search.seq++; // 늦게 도착하는 검색 결과는 버립니다
  search.mode = 'history';
  search.list = search.history;
  search.active = -1;
  renderSuggest();
  try {
    const { items } = await api('/api/history');
    search.history = items;
    learnNames(items);
    if (search.mode === 'history') {
      search.list = items;
      renderSuggest();
    }
  } catch (e) { /* 기록을 못 읽어도 검색은 됩니다 */ }
}

async function runSearch(q) {
  const seq = ++search.seq;
  let items = [];
  let emptyText = '일치하는 종목이 없습니다';
  try {
    const res = await api(`/api/symbols?q=${encodeURIComponent(q)}&limit=20`);
    items = res.items;
    if (!res.ready) emptyText = '전체 종목 목록을 아직 받지 못해 일부 종목만 찾을 수 있습니다';
  } catch (e) {
    emptyText = `검색 실패: ${e.message}`;
  }
  if (seq !== search.seq || document.activeElement !== symbolInput) return;
  learnNames(items);
  Object.assign(search, { mode: 'results', list: items, active: items.length ? 0 : -1, query: q, emptyText });
  renderSuggest();
}

function refreshSuggest() {
  clearTimeout(search.timer);
  const q = symbolInput.value.trim();
  if (!q || q === symbolLabel(state.code)) openHistory();
  else search.timer = setTimeout(() => runSearch(q), 120);
}

async function submitSearch() {
  clearTimeout(search.timer);
  const text = symbolInput.value.trim();
  const shown = search.mode === 'history' || (search.mode === 'results' && search.query === text);
  if (shown && search.active >= 0) return choose(search.list[search.active]);
  if (!text || text === symbolLabel(state.code)) {
    closeSuggest();
    symbolInput.blur();
    return;
  }
  const code = parseCode(text);
  if (code) return choose({ code, name: names.get(code) || '' });
  await runSearch(text); // 결과가 오기 전에 Enter 를 누른 경우: 첫 번째 결과로 엽니다
  if (search.query === text && search.list.length) choose(search.list[0]);
}

function choose(item) {
  clearTimeout(search.timer);
  closeSuggest();
  symbolInput.blur();
  switchCode(item.code, { name: item.name, record: true });
}

async function addHistory(code, name) {
  try {
    const { items } = await api('/api/history', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ code, name }),
    });
    search.history = items;
    learnNames(items);
  } catch (e) { /* 기록을 못 남겨도 차트는 그대로 봅니다 */ }
}

async function removeHistory(code) {
  try {
    const { items } = await api(`/api/history/${encodeURIComponent(code)}`, { method: 'DELETE' });
    search.history = items;
  } catch (e) {
    return;
  }
  if (search.mode === 'history') {
    search.list = search.history;
    search.active = Math.min(search.active, search.list.length - 1);
    renderSuggest();
  }
}

async function clearHistory() {
  if (!confirm('검색 기록을 모두 지울까요?')) return;
  try {
    const { items } = await api('/api/history', { method: 'DELETE' });
    search.history = items;
  } catch (e) {
    return;
  }
  if (search.mode === 'history') {
    search.list = [];
    search.active = -1;
    renderSuggest();
  }
}

function setupSearch() {
  let selectOnMouseUp = false;
  symbolInput.addEventListener('mousedown', () => {
    selectOnMouseUp = document.activeElement !== symbolInput;
  });
  symbolInput.addEventListener('mouseup', (e) => {
    if (selectOnMouseUp) e.preventDefault(); // 클릭으로 들어왔을 때 전체 선택이 풀리지 않게
    selectOnMouseUp = false;
  });
  symbolInput.addEventListener('focus', () => {
    symbolInput.select();
    openHistory();
  });
  symbolInput.addEventListener('input', refreshSuggest);
  symbolInput.addEventListener('keydown', (e) => {
    if (e.isComposing) return; // 한글 조합 중인 Enter·방향키는 입력기에 맡깁니다
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (search.mode === 'closed') return refreshSuggest();
      const n = search.list.length;
      if (!n) return;
      search.active = e.key === 'ArrowDown' ? (search.active + 1) % n : (search.active - 1 + n) % n;
      markActive(true);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      submitSearch();
    } else if (e.key === 'Escape') {
      closeSuggest();
      symbolInput.blur();
    }
  });
  symbolInput.addEventListener('blur', () => {
    setTimeout(() => {
      if (document.activeElement === symbolInput) return;
      closeSuggest();
      showSymbol(state.code);
    }, 150);
  });
  suggest.addEventListener('mousedown', (e) => e.preventDefault()); // 목록을 눌러도 입력칸 포커스 유지
}

async function switchCode(code, { name = '', record = false } = {}) {
  if (!isCode(code || '')) {
    setStatus('error', '종목명 일부, 6자리 종목코드, 해외 티커를 입력해 목록에서 고르세요');
    return;
  }
  if (name) names.set(code, name);
  state.code = code;
  localStorage.setItem('kis.code', code);
  showSymbol(code);
  if (!names.has(code)) lookupName(code);
  if (record) addHistory(code, names.get(code) || '');
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
  await switchCode(parseCode(state.code) || '005930');
}

window.__app = { state, chart, candles, drawings, timeMap }; // 디버깅용
init();
