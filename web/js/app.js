import { TIMEFRAMES, bucketStart, isMinuteTf, tfSeconds } from './timeframe.js';
import { TimeMap } from './timemap.js';
import { DrawingManager, setPriceDecimals } from './drawings.js';
import { createDrawingPersistence } from './drawing-persistence.js';
import { setupDrawingSync } from './drawing-sync.js';
import { setupDrawingToolbar } from './toolbar.js';
import { setupChartTypes } from './chart-types.js';
import { createIndicators, INDICATORS } from './indicators.js';
import { setupIndicatorLibrary } from './indicator-library.js';
import { setupReplay, setupPriceAlerts } from './chart-actions.js';
import { mergeQuoteWindow, readQuoteWindow } from './quote-updates.js';

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
  quoteTransport: 'mock',
  quoteStatus: null,
  config: null,
  loadSeq: 0,
  loadedSeq: 0,
};

// 주식과 지수는 거래소나 심볼이 겹쳐도 별개의 차트로 저장합니다.
const DOMESTIC_RE = /^(?=.*\d)[0-9A-Z]{6}$/;
const OVERSEAS_RE = /^(NAS|NYS|AMS):[A-Z0-9][A-Z0-9.-]{0,15}$/;
const INDEX_RE = /^INDEX:(?:\d{4}|(?=.{1,10}$)\.?[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*)$/;
const isIndex = code => INDEX_RE.test(code);
const isYahoo = code => /^YF:(?=.{1,24}$)\^?[A-Z0-9][A-Z0-9.-]*(?:=[FX])?$/.test(code);
const isCode = code => DOMESTIC_RE.test(code) || OVERSEAS_RE.test(code) || isIndex(code) || isYahoo(code);
const isChartIndex = code => isIndex(code) || (isYahoo(code) && !code.includes('='));
const usesYahooQuotes = code => isYahoo(code) || state.config?.source === 'yahoo';
const usesHttpQuotes = code => usesYahooQuotes(code) || state.quoteTransport === 'https';
const yahooPrecision = new Map();
const tickerOf = code => code.slice(code.indexOf(':') + 1);
const isUsIndex = code => isIndex(code) && !/^INDEX:\d{4}$/.test(code);
const indexLabel = code => isUsIndex(code) ? '미국 지수' : '국내 지수';
const paperIndexRestriction = () => isIndex(state.code) && state.config?.source === 'kis' && state.config.env === 'demo'
  ? '한투 모의투자의 지수 차트는 일·주·월봉을 제공합니다.' : '';

// 가격 표시: 국내는 원 단위, 해외는 달러 소수 2자리 (1달러 미만은 4자리)
let priceDecimals = 0;
const fmtPrice = (p) => p.toLocaleString('ko-KR', { minimumFractionDigits: priceDecimals, maximumFractionDigits: priceDecimals });
const fmtInt = (x) => Math.round(x).toLocaleString('ko-KR');

// ------------------------------------------------------------ 차트
const container = $('#chart');
const chart = LC.createChart(container, {
  autoSize: true,
  layout: { attributionLogo: false, background: { color: '#131722' }, textColor: '#d1d4dc', fontFamily: '-apple-system, "Segoe UI", "Malgun Gothic", sans-serif' },
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
let alerts;
const replay = setupReplay(chart, () => state.loadedSeq === state.loadSeq ? state.bars : [], () => {
  renderBars({ resetRange: true });
  alerts.reset();
  paintLive();
});
function displayedBars() { return replay.bars || state.bars; }
const drawings = new DrawingManager({
  chart, series: candles, container, timeMap, getBars: displayedBars,
});
const chartTypes = setupChartTypes({ chart, candles, getBars: displayedBars, getTimeframe: () => state.tf,
  setTimeframe: switchTf, intradayRestriction: paperIndexRestriction, mount: $('#chart-type-control') });
const studies = createIndicators({ chart, container,
  openStudyAlert: target => alerts.openStudy(target),
  onStudyAlertReset: (id, reason) => alerts?.targetChanged('study', id, reason),
});
let projectionBars = studies.projectionBars;
function chartRightOffset() { return Math.max(12, projectionBars + 2); }
function rangeWithProjection(range, last) {
  if (!projectionBars || last < range.from || last > range.to) return range;
  // Leave at least two thirds of a zoomed-in chart for the price history.
  const room = Math.min(chartRightOffset(), (range.to - range.from) / 3);
  const move = Math.max(0, last + room - range.to);
  return { from: range.from + move, to: range.to + move };
}
chart.timeScale().applyOptions({ rightOffset: chartRightOffset() });
studies.subscribe(() => {
  if (studies.projectionBars === projectionBars) return;
  projectionBars = studies.projectionBars;
  const scale = chart.timeScale();
  const range = scale.getVisibleLogicalRange();
  scale.applyOptions({ rightOffset: chartRightOffset() }); // Also include the cloud when using Fit Content.
  if (range) scale.setVisibleLogicalRange(rangeWithProjection(range, displayedBars().length - 1));
});
const indicatorLibrary = setupIndicatorLibrary({
  button: $('#indicators-button'), favoritesButton: $('#indicator-favorites'), catalog: INDICATORS,
  onAdd: id => studies.add(id), has: id => studies.has(id),
  onPersonalScriptsChange: entries => studies.setPersonalScripts(entries),
});
alerts = setupPriceAlerts(() => ({
  key: usesYahooQuotes(state.code) ? 'yahoo/' + state.code : [state.config?.source, state.config?.env, DOMESTIC_RE.test(state.code) ? state.config?.market : '', state.code].join('/'),
  label: symbolLabel(state.code), price: state.bars.at(-1)?.close, formatPrice: usesYahooQuotes(state.code) ? fmtPrice : null,
  allowNegative: isYahoo(state.code) && state.code.endsWith('=F'),
  code: state.code, tf: state.tf, time: state.bars.at(-1)?.time,
  chartReady: state.loadedSeq === state.loadSeq && !replay.bars,
}), {
  studyTarget: studies.alertTarget, studyValue: studies.alertValue,
  drawingTarget: id => drawings.alertTarget(id), drawingValue: (id, time) => drawings.alertValue(id, time),
});
drawings.onAlertTargetChange = (id, reason) => alerts.targetChanged('drawing', id, reason);

const volBar = (b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(239,83,80,.45)' : 'rgba(41,98,255,.45)' });

// ------------------------------------------------------------ 데이터 로드
let older = { hasMore: false, loading: false, error: '' };
let chartRequest;
let dateRequest;
let selectedBarTime = null;
let hoveredBarTime = null;
let rangeFrame;
let fitPriceForNewSymbol = false;
const nearHistoryEdge = range => range && range.from < 16;
chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
  cancelAnimationFrame(rangeFrame);
  rangeFrame = requestAnimationFrame(() => {
    updateLegend();
    if (state.loadedSeq !== state.loadSeq || !state.bars.length || replay.bars) return;
    if (nearHistoryEdge(chart.timeScale().getVisibleLogicalRange())) loadOlderBars();
    showBarCount();
  });
});

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `HTTP ${r.status}`);
  return body;
}

async function chartData(path, code, tf, { before, since, fromDate, signal } = {}) {
  const params = new URLSearchParams({ code, tf });
  if (before != null) params.set('before', before);
  if (since != null) params.set('since', since);
  if (fromDate != null) params.set('from_date', fromDate);
  const page = await api(`${path}?${params}`, { signal });
  if (usesYahooQuotes(code) && page.source !== 'yahoo') throw new Error('Yahoo Finance 시세 출처를 확인할 수 없습니다.');
  return page;
}

function applyPriceFormat(code, bars) {
  const last = bars.length ? bars[bars.length - 1].close : 0;
  let d = 0;
  if (isYahoo(code)) d = yahooPrecision.get(code) ?? (code.endsWith('=X') ? 5 : 2);
  else if (isIndex(code)) d = 2;
  else if (OVERSEAS_RE.test(code)) d = last > 0 && last < 1 ? 4 : 2;
  if (d === priceDecimals) return;
  priceDecimals = d;
  setPriceDecimals(d);
  candles.applyOptions({ priceFormat: { type: 'price', precision: d, minMove: 1 / 10 ** d } });
}

function renderStudies(bars, tailOnly = false) {
  studies.setData(bars, { code: state.code, tf: state.tf, isIndex: isChartIndex(state.code),
    rawVolume: isYahoo(state.code), pricePrecision: usesYahooQuotes(state.code) ? priceDecimals : undefined }, tailOnly);
}

function renderLatestBar() {
  const bars = state.bars, bar = bars.at(-1);
  applyPriceFormat(state.code, bars);
  timeMap.push(bar.time);
  candles.update({ ...bar });
  volume.update(volBar(bar));
  renderStudies(bars, true);
  updateLegend();
  $('#replay-button').disabled = bars.length < 2;
  updateDateJump();
}

function renderBars({ range = null, resetRange = false } = {}) {
  const bars = displayedBars();
  applyPriceFormat(state.code, bars);
  timeMap.setBars(bars, tfSeconds(state.tf), state.tf === 'D');
  candles.setData(bars.map(bar => ({ ...bar })));
  volume.setData(bars.map(volBar));
  renderStudies(bars);
  if (range) chart.timeScale().setVisibleLogicalRange(range);
  else if (resetRange && bars.length) {
    const range = { from: bars.length - 150, to: bars.length + 10 };
    chart.timeScale().setVisibleLogicalRange(rangeWithProjection(range, bars.length - 1));
  }
  if (fitPriceForNewSymbol && bars.length && state.loadedSeq === state.loadSeq) {
    if ($('#fit-price-on-symbol').checked) candles.priceScale().setAutoScale(true);
    fitPriceForNewSymbol = false;
  }
  drawings.update();
  updateLegend();
  $('#replay-button').disabled = !replay.bars && bars.length < 2;
  updateDateJump();
}

async function loadOlderBars() {
  if (!older.hasMore || older.loading || older.error || replay.bars || state.loadedSeq !== state.loadSeq) return;
  const before = state.bars[0]?.time;
  if (before == null) return;
  const seq = state.loadSeq;
  older.loading = true;
  showBarCount();
  try {
    const page = await chartData('/api/candles', state.code, state.tf, { before, signal: chartRequest.signal });
    if (seq !== state.loadSeq) return;
    const first = state.bars[0].time;
    const prefix = page.bars.filter(bar => bar.time < first);
    if (first === before || prefix.length) older.hasMore = Boolean(prefix.length && page.has_more);
    if (prefix.length) {
      // Anchor the range at response time, including any drag or zoom while the page was loading.
      const range = chart.timeScale().getVisibleLogicalRange();
      state.bars = prefix.concat(state.bars);
      if (!replay.bars) {
        renderBars({ range: range && { from: range.from + prefix.length, to: range.to + prefix.length } });
        alerts.reset();
      }
    }
  } catch (error) {
    if (seq === state.loadSeq) older.error = error.message;
  } finally {
    if (seq === state.loadSeq) {
      older.loading = false;
      showBarCount();
    }
  }
}

async function loadCandles({ keepRange = false, preserveHistory = false } = {}) {
  const seq = ++state.loadSeq;
  hoveredBarTime = null;
  if (!keepRange) selectedBarTime = null;
  dateRequest?.abort();
  if ($('#date-dialog').open) $('#date-dialog').close();
  updateDateJump();
  state.quoteStatus = null;
  paintQuoteStatus();
  chartRequest?.abort();
  chartRequest = new AbortController();
  const previous = older;
  older = { hasMore: false, loading: false, error: '' };
  setStatus('loading', '차트 불러오는 중…');
  try {
    const { bars, has_more, quote_status, earliest_available } = await chartData('/api/candles', state.code, state.tf, { signal: chartRequest.signal });
    if (seq !== state.loadSeq) return;
    const previousBars = state.bars;
    let range = keepRange ? chart.timeScale().getVisibleLogicalRange() : null;
    const prefix = preserveHistory ? state.bars.filter(bar => !bars.length || bar.time < bars[0].time) : [];
    older.hasMore = prefix.length ? previous.hasMore : Boolean(has_more);
    older.firstAvailable = earliest_available ?? (prefix.length ? previous.firstAvailable : null);
    state.loadedSeq = seq;
    state.bars = prefix.concat(bars);
    state.quoteStatus = quote_status ?? null;
    paintQuoteStatus();
    if (range && previousBars.length) {
      const anchor = Math.max(0, Math.min(previousBars.length - 1, Math.round((range.from + range.to) / 2)));
      const time = previousBars[anchor].time;
      const next = state.bars.findIndex(bar => bar.time >= time);
      range = next < 0 || time < state.bars[0].time ? null
        : { from: range.from + next - anchor, to: range.to + next - anchor };
    }
    if (!replay.bars) renderBars({ range, resetRange: !range });
    alerts.reset();
    showBarCount();
  } catch (e) {
    if (seq === state.loadSeq) {
      if (preserveHistory && state.bars.length) {
        state.loadedSeq = seq;
        older.hasMore = previous.hasMore;
      } else {
        state.bars = [];
        if (!replay.bars) renderBars();
      }
      setStatus('error', `불러오기 실패: ${e.message}`);
    }
  }
}

const drawingPath = code => '/api/drawings/' + encodeURIComponent(code);
const drawingStore = createDrawingPersistence({
  mergeChanges: () => Boolean(state.config?.drawing_sync_role),
  storage: localStorage,
  read: code => api(drawingPath(code)),
  async write(code, base, text) {
    const response = await fetch(drawingPath(code), {
      method: 'PUT', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ base_revision: base, drawings: JSON.parse(text) }),
    });
    const data = await response.json();
    if (response.status === 409) return { ...data, conflict: true };
    if (!response.ok) throw new Error(data.detail || '그림 저장 실패');
    return data;
  },
  display: list => drawings.load(list),
  status(message, retry, conflict) {
    $('#saved').textContent = message;
    $('#drawing-retry').hidden = !retry;
    $('#drawing-keep').hidden = $('#drawing-reload').hidden = !conflict;
  },
});
drawings.onChange = () => drawingStore.change(drawings.toJSON());
$('#drawing-retry').onclick = () => drawingStore.retry();
$('#drawing-keep').onclick = () => {
  const prompt = state.config?.drawing_sync_role ? '충돌한 그림에 이 창의 편집을 적용할까요?' : '서버에 저장된 그림을 현재 편집본으로 바꿀까요?';
  if (confirm(prompt)) drawingStore.resolve(true);
};
$('#drawing-reload').onclick = () => {
  const prompt = state.config?.drawing_sync_role ? '충돌한 그림에 서버의 편집을 적용할까요?' : '현재 편집본을 버리고 서버의 그림을 불러올까요?';
  if (confirm(prompt)) drawingStore.resolve(false);
};
window.addEventListener('online', drawingStore.recover);
document.addEventListener('visibilitychange', () => { if (document.hidden) drawingStore.flushAll(); });
window.addEventListener('pagehide', () => drawingStore.flushAll());
window.addEventListener('beforeunload', event => {
  if (drawingStore.unprotectedEdits) { event.preventDefault(); event.returnValue = true; }
});
drawingStore.recover();

// ------------------------------------------------------------ 실시간
function applyTick({ t, p, v }) {
  const bars = state.bars;
  if (!bars.length) return;
  const last = bars[bars.length - 1];
  const bNew = bucketStart(t, state.tf);
  const bLast = bucketStart(last.time, state.tf);
  if (bNew === bLast) {
    last.high = Math.max(last.high, p);
    last.low = Math.min(last.low, p);
    last.close = p;
    last.volume += v;
  } else if (bNew > bLast) {
    bars.push({ time: bNew, open: p, high: p, low: p, close: p, volume: v });
  } else {
    return; // 이미 지난 봉의 틱
  }
  if (!replay.bars) renderLatestBar();
  alerts.observeBars(bars, bars.length - 1);
}

let quoteRequest;
function stopQuotes() {
  quoteRequest?.abort();
  if (state.ws) {
    state.ws.onopen = state.ws.onmessage = state.ws.onclose = null;
    state.ws.close();
    state.ws = null;
  }
  clearTimeout(state.wsRetry);
}

function followQuotes() {
  stopQuotes();
  const code = state.code;
  if (usesHttpQuotes(code)) {
    const controller = new AbortController();
    quoteRequest = controller;
    const yahoo = usesYahooQuotes(code);
    const tf = state.tf;
    const seq = state.loadSeq;
    const pollingInfo = yahoo ? 'Yahoo Finance 시세를 1분 간격으로 조회합니다. 거래소에 따라 지연될 수 있습니다.'
      : '한투 HTTPS 시세를 5초 간격으로 조회합니다.';
    const interval = yahoo ? 60000 : 5000;
    let delay = interval;
    const current = () => !controller.signal.aborted && seq === state.loadSeq && code === state.code && tf === state.tf;
    async function refresh() {
      if (!current()) return;
      try {
        const initial = state.loadedSeq !== seq || !state.bars.length;
        const page = initial ? await chartData('/api/candles', code, tf, { signal: controller.signal })
          : await readQuoteWindow((since, before) => chartData('/api/quotes/latest', code, tf,
            { since, before, signal: controller.signal }), state.bars.at(-1).time);
        if (!current()) return;
        if (initial) older.hasMore = Boolean(page.has_more);
        state.loadedSeq = seq;
        state.quoteStatus = page.quote_status ?? null;
        if (page.bars.length) {
          const update = mergeQuoteWindow(state.bars, page.bars, page.recovery);
          if (update.changes && !replay.bars) {
            if (update.tailOnly && !initial) renderLatestBar();
            else renderBars({ resetRange: initial });
          }
          if (page.recovery === 'unavailable') older.quoteGap = true;
          if (update.reset || initial) alerts.reset();
          else if (update.changes) alerts.observeBars(state.bars, update.observeFrom, update.rebase ? update.anchor : -1);
        }
        showBarCount();
        setLive('secure', pollingInfo);
        delay = interval;
      } catch (error) {
        if (!current()) return;
        state.quoteStatus = null;
        setLive('error', `시세 갱신 실패 · ${error.message}`);
        delay = Math.min(delay * 2, yahoo ? 300000 : 60000);
      }
      state.wsRetry = setTimeout(refresh, delay);
    }
    setLive('secure', pollingInfo);
    state.wsRetry = setTimeout(refresh, delay);
    return;
  }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/ticks?code=${encodeURIComponent(code)}`);
  state.ws = ws;
  let opened = false;
  ws.onopen = () => {
    opened = true;
    setLive('connecting', '데모 시세 연결 중');
  };
  ws.onmessage = (ev) => {
    // 종목을 바꾼 뒤 새 차트를 불러오는 동안에도 이전 종목의 연결이 잠시 남아 있습니다.
    // 그 틱이 새 종목의 봉에 섞이지 않게 버립니다.
    if (state.code !== code || state.loadedSeq !== state.loadSeq) return;
    const msg = JSON.parse(ev.data);
    if (msg.type === 'tick') {
      applyTick(msg);
      setLive('live');
    } else if (msg.type === 'status') {
      setLive(msg.state, msg.msg);
    } else if (msg.type === 'resync') {
      loadCandles({ keepRange: true, preserveHistory: true });
    }
  };
  ws.onclose = () => {
    setLive('off', '데모 시세 연결이 끊겨 다시 연결합니다.');
    state.wsRetry = setTimeout(() => {
      if (state.code !== code) return;
      followQuotes();
      if (opened) loadCandles({ keepRange: true, preserveHistory: true }); // 끊긴 사이의 봉을 다시 받습니다
    }, 2000);
  };
}

// ------------------------------------------------------------ 화면 요소
function setStatus(kind, text) {
  const el = $('#status');
  el.textContent = text;
  el.dataset.kind = kind;
  el.title = text;
  $('#history-retry').hidden = kind !== 'history-error';
  $('#index-daily').hidden = !(['empty', 'error'].includes(kind) && !state.bars.length
    && isIndex(state.code) && isMinuteTf(state.tf) && state.config?.source === 'kis');
}

function isoDay(time) { return new Date(time * 1000).toISOString().slice(0, 10); }

function updateDateJump() {
  const button = $('#date-jump');
  button.hidden = !usesYahooQuotes(state.code) || isMinuteTf(state.tf);
  button.disabled = Boolean(replay.bars) || state.loadedSeq !== state.loadSeq || !state.bars.length;
}

function setupDateJump() {
  const dialog = $('#date-dialog');
  const input = $('#chart-date');
  const feedback = $('#date-feedback');
  const submit = $('#date-submit');
  $('#date-jump').onclick = () => {
    const visible = chart.timeScale().getVisibleLogicalRange();
    const center = visible ? Math.round((visible.from + visible.to) / 2) : state.bars.length - 1;
    input.value = isoDay(state.bars[Math.max(0, Math.min(center, state.bars.length - 1))].time);
    feedback.textContent = '1980이나 1980-01-02처럼 입력하세요.';
    submit.disabled = false;
    dialog.showModal();
    input.select();
  };
  $('#date-cancel').onclick = () => dialog.close();
  dialog.addEventListener('close', () => dateRequest?.abort());
  $('#date-form').onsubmit = async event => {
    event.preventDefault();
    const value = input.value.trim();
    const date = /^\d{4}$/.test(value) ? value + '-01-01' : value;
    const day = Date.parse(date + 'T00:00:00Z') / 1000;
    const now = new Date();
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) / 1000;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(day) || isoDay(day) !== date
      || date < '1800-01-01' || day > today) {
      feedback.textContent = '1800년부터 오늘까지의 연도나 날짜를 입력하세요.';
      return;
    }
    const before = state.bars[0]?.time;
    if (before == null || replay.bars || submit.disabled) return;
    const bucket = bucketStart(day, state.tf);
    let appended = false;
    if (bucket < before && (older.hasMore || older.firstAvailable == null)) {
      dateRequest?.abort();
      const request = new AbortController();
      dateRequest = request;
      submit.disabled = true;
      feedback.textContent = '과거 시세를 불러오는 중…';
      try {
        const page = await chartData('/api/candles', state.code, state.tf, { before, fromDate: day, signal: request.signal });
        if (request.signal.aborted || !dialog.open || replay.bars) return;
        // Ordinary left-edge paging or current quotes may finish during this request.
        const first = state.bars[0].time;
        const prefix = page.bars.filter(bar => bar.time < first);
        older.firstAvailable = page.earliest_available;
        if (first === before || prefix.length) older.hasMore = Boolean(page.has_more);
        if (prefix.length) {
          state.bars = prefix.concat(state.bars);
          appended = true;
          older.error = '';
        }
      } catch (error) {
        if (!request.signal.aborted) feedback.textContent = `이동할 날짜를 불러오지 못했습니다. ${error.message}`;
        return;
      } finally {
        if (dateRequest === request) {
          dateRequest = null;
          submit.disabled = false;
        }
      }
    }
    const index = state.bars.findIndex(bar => bar.time >= bucket);
    const target = index < 0 ? state.bars.length - 1 : index;
    selectedBarTime = state.bars[target].time;
    hoveredBarTime = null;
    const scale = chart.timeScale();
    const visible = scale.getVisibleLogicalRange();
    const width = visible ? visible.to - visible.from : 160;
    const from = Math.max(0, Math.min(target - width / 2, state.bars.length - width + chartRightOffset()));
    const range = { from, to: from + width };
    if (appended) {
      renderBars({ range });
      alerts.reset();
    }
    else scale.setVisibleLogicalRange(range);
    updateLegend();
    dialog.close();
    showBarCount();
  };
}

function showBarCount() {
  const count = `${state.bars.length}개 봉` + (older.quoteGap ? ' · 시세 공백 있음' : '');
  const nxtPeriod = state.config?.source === 'kis' && state.config.market === 'NX'
    && DOMESTIC_RE.test(state.code) && !isMinuteTf(state.tf);
  if (!state.bars.length && nxtPeriod) return setStatus('empty', '이 종목의 NXT 시세가 없습니다. 설정에서 KRX를 선택할 수 있습니다.');
  if (!state.bars.length && usesYahooQuotes(state.code)) return setStatus('empty', 'Yahoo Finance에서 이 기간의 시세를 제공하지 않습니다.');
  if (!state.bars.length && isIndex(state.code)) return setStatus('empty', '이 기간에 조회된 지수 봉이 없습니다.');
  if (state.bars.length && !replay.bars && nearHistoryEdge(chart.timeScale().getVisibleLogicalRange())) {
    if (older.loading) return setStatus('loading', '이전 차트 불러오는 중…');
    if (older.error) return setStatus('history-error', `이전 차트 조회 실패 · ${older.error}`);
    if (!older.hasMore) {
      const first = new Date(state.bars[0].time * 1000).toISOString().slice(0, 10);
      if (usesYahooQuotes(state.code) && Number.isFinite(older.firstAvailable)) {
        return setStatus('ok', `${count} · Yahoo Finance 첫 시세 ${isoDay(older.firstAvailable)}`);
      }
      if (nxtPeriod) return setStatus('ok', `NXT 첫 봉 ${first} · 더 오래된 한국거래소 시세는 설정에서 KRX를 선택하세요.`);
      return setStatus('ok', `${count} · 조회 가능한 첫 봉 ${first}`);
    }
  }
  setStatus('ok', count);
}
let liveState = { kind: 'connecting', text: '' };
function setLive(kind, text) {
  liveState = { kind, text };
  paintLive();
  if (kind === 'error') setStatus('error', text);
}

function paintLive() {
  paintQuoteStatus();
  const { kind, text } = liveState;
  const el = $('#live');
  if (replay.bars) {
    el.dataset.kind = 'replay';
    el.title = '과거 시세 재생 중';
    $('#live-text').textContent = '리플레이';
    return;
  }
  el.dataset.kind = kind;
  el.title = kind === 'live' ? '앱에서 생성한 가격과 거래량을 갱신합니다.' : text || '';
  const secureLabel = usesYahooQuotes(state.code) ? '1분 갱신' : 'HTTPS · 5초';
  $('#live-text').textContent = kind === 'secure' ? secureLabel : kind === 'live' ? '자동 갱신' : kind === 'error' ? '오류' : kind === 'off' ? '끊김' : '연결 중';
}

function paintQuoteStatus() {
  const badge = $('#quote-delay');
  const info = state.quoteStatus;
  let label = '', title = '', kind = 'delayed';
  if (info && state.bars.length && !replay.bars
    && usesHttpQuotes(state.code)) {
    const venue = usesYahooQuotes(state.code) ? 'Yahoo Finance' : '한국투자증권';
    const closed = info.market_open === false;
    const session = info.market_state === 'pre' ? '정규장 전' : info.market_state === 'post' ? '정규장 후' : '장 마감';
    if (info.end_of_day === true) {
      kind = 'close';
      label = '종가 시세';
      title = `${venue}에서 장 마감 종가를 제공합니다.`;
    } else if (Number.isInteger(info.delay_minutes) && info.delay_minutes > 0) {
      label = closed ? `${session} · ${info.delay_minutes}분 지연 시세` : `${info.delay_minutes}분 지연`;
      title = info.delay_source === 'exchange' ? `${venue} · 거래소 공시 기준 지연 시간입니다.`
        : `${venue}에서 안내한 종목 시세 지연 시간입니다.`;
    } else if (info.feed === 'delayed' || (info.delay_minutes !== 0 && info.delayed === true)) {
      label = closed ? `${session} · 지연 시세` : '지연 시세';
      title = `${venue}에서 지연 시세로 제공합니다.`;
    } else if (info.delayed_or_close === true && info.market_open !== false) {
      kind = 'close';
      label = '지연 또는 종가 시세';
      title = `${venue}에서 지연 시세 또는 종가로 제공합니다. 정확한 지연 시간은 공개되지 않습니다.`;
    } else if (info.market_open === true && (info.stale === true || (info.stale == null
      && info.delay_minutes !== 0 && info.feed !== 'realtime' && info.delayed !== false))
      && isMinuteTf(state.tf) && Number.isFinite(info.as_of) && info.as_of > 0) {
      const age = Math.floor((Date.now() / 1000 - info.as_of) / 60);
      if (age >= (info.stale === true ? 1 : 5)) {
        kind = 'observed';
        label = `최근 시세 ${age}분 전`;
        title = `${venue} 마지막 시세 시각 · ${new Date(info.as_of * 1000).toLocaleString('ko-KR')}`;
      }
    }
  }
  if (badge.textContent !== label) badge.textContent = label;
  badge.hidden = !label;
  badge.dataset.kind = kind;
  badge.title = title;
}

function updateLegend() {
  const bars = displayedBars();
  if (!bars.length) { $('#legend').textContent = ''; return; }
  const range = chart.timeScale().getVisibleLogicalRange();
  const last = bars.length - 1;
  const end = range ? Math.max(0, Math.min(last, Math.floor(range.to))) : last;
  const anchor = hoveredBarTime ?? selectedBarTime;
  const selected = anchor == null ? -1 : Math.round(timeMap.toLogical(anchor));
  const inView = range && selected >= Math.floor(range.from) && selected <= Math.ceil(range.to);
  const i = inView && bars[selected]?.time === anchor ? selected : end;
  const b = bars[i];
  const prev = i > 0 ? bars[i - 1] : null;
  const chg = prev ? b.close - prev.close : 0;
  const pct = prev ? (chg / prev.close) * 100 : 0;
  const f = fmtPrice;
  const cls = chg > 0 ? 'up' : chg < 0 ? 'down' : '';
  $('#legend').innerHTML =
    (i < last ? `<span class="legend-date">${isoDay(b.time)}</span>` : '') +
    `<span>시 <b>${f(b.open)}</b></span><span>고 <b>${f(b.high)}</b></span>` +
    `<span>저 <b>${f(b.low)}</b></span><span>종 <b class="${cls}">${f(b.close)}</b>${isChartIndex(state.code) ? ' pt' : ''}</span>` +
    `<span class="${cls}">${chg >= 0 ? '+' : ''}${f(chg)} (${pct.toFixed(2)}%)</span>` +
    `<span>거래량 <b>${(isChartIndex(state.code) || state.code.endsWith('=X')) && !b.volume ? '—' : fmtInt(b.volume)}</b></span>`;
}

chart.subscribeCrosshairMove((param) => {
  hoveredBarTime = typeof param?.time === 'number' ? param.time : null;
  updateLegend();
});

function buildToolbar() {
  const tfBox = $('#tfs');
  for (const t of TIMEFRAMES) {
    const b = document.createElement('button');
    b.textContent = t.label;
    b.dataset.tf = t.id;
    b.onclick = () => { $('#interval-control').open = false; switchTf(t.id); };
    tfBox.insertBefore(b, $('#index-interval-note'));
  }
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('#interval-control')) $('#interval-control').open = false;
  });
  $('#interval-control').addEventListener('keydown', event => {
    if (event.key === 'Escape') { $('#interval-control').open = false; $('#interval-label').focus(); }
  });
  setupDrawingToolbar(drawings, chart, target => alerts.openDrawing(target));
  setupSearch();
}

// ------------------------------------------------------------ 종목 검색 · 검색 기록
const names = new Map(); // 종목코드 -> 종목명 (검색 결과·기록·서버 조회에서 알게 된 이름)
const savedYahooKey = 'kis.lastYahooSymbol';
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
  if (isYahoo(code) && yahooPrecision.has(code)) {
    try {
      localStorage.setItem(savedYahooKey, JSON.stringify({ code, name: names.get(code), precision: yahooPrecision.get(code) }));
    } catch { /* 현재 차트에서는 조회한 정밀도를 그대로 사용합니다. */ }
  }
}

function learnNames(items) {
  for (const it of items) {
    if (it.name) names.set(it.code, it.name);
    if (isYahoo(it.code) && Number.isInteger(it.precision) && it.precision >= 0 && it.precision <= 10) {
      yahooPrecision.set(it.code, it.precision);
    }
  }
}

async function lookupName(code) {
  try {
    const s = await api(`/api/symbols/${encodeURIComponent(code)}`);
    const oldPrecision = yahooPrecision.get(code);
    learnNames([s]);
    if (state.code === code) {
      showSymbol(code);
      if (oldPrecision !== yahooPrecision.get(code) && state.loadedSeq === state.loadSeq && displayedBars().length) renderBars();
    }
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

function symbolKind(item) {
  const kind = isIndex(item.code) ? indexLabel(item.code)
    : item.kind && item.kind !== '주식' ? item.kind : item.market;
  if (usesYahooQuotes(item.code)) return [kind, 'Yahoo'].filter(Boolean).join(' · ');
  if (item.code === 'INDEX:VIX') return kind + (state.config?.source === 'kis' ? ' · 한투' : ' · 데모');
  return kind || '';
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
      el('span', 'sg-kind', symbolKind(item)));
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
  if (seq !== search.seq || document.activeElement !== symbolInput || symbolInput.value.trim() !== q) return;
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
  const shown = (search.mode === 'history' && (!text || text === symbolLabel(state.code)))
    || (search.mode === 'results' && search.query === text);
  if (shown && search.active >= 0) return choose(search.list[search.active]);
  if (!text || text === symbolLabel(state.code)) {
    closeSuggest();
    symbolInput.blur();
    return;
  }
  const code = parseCode(text);
  if (code && (names.has(code) || OVERSEAS_RE.test(code) || /^\d{6}$/.test(code))) {
    return choose({ code, name: names.get(code) || '' });
  }
  await runSearch(text); // 결과가 오기 전에 Enter 를 누른 경우: 첫 번째 결과로 엽니다
  if (search.mode !== 'results' || search.query !== text || symbolInput.value.trim() !== text) return;
  if (search.list.length) choose(search.list[0]);
  else if (code && DOMESTIC_RE.test(code)) choose({ code, name: '' }); // 마스터에 없는 국내 종목
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
    setStatus('error', '종목·지수 이름이나 코드를 입력해 목록에서 고르세요');
    return;
  }
  replay.stop(false);
  stopQuotes();
  setLive('connecting', '시세를 불러오는 중입니다.');
  if (state.code !== code) {
    fitPriceForNewSymbol = true;
    state.bars = [];
    renderBars();
  }
  if (name) names.set(code, name);
  state.code = code;
  const preferred = localStorage.getItem('kis.tf') || 'D';
  const available = TIMEFRAMES.some(t => t.id === preferred) && !(isMinuteTf(preferred) && paperIndexRestriction());
  applyTimeframe(available ? preferred : 'D');
  updateSourceBadge();
  localStorage.setItem('kis.code', code);
  showSymbol(code);
  if (!names.has(code) || isYahoo(code)) lookupName(code);
  if (record) addHistory(code, names.get(code) || '');
  const loading = loadCandles();
  const seq = state.loadSeq;
  await Promise.all([loading, drawingStore.select(code)]);
  if (state.loadSeq === seq) followQuotes();
}

function applyTimeframe(tf) {
  state.tf = tf;
  updateDateJump();
  const restriction = paperIndexRestriction();
  const label = $('#interval-label');
  label.textContent = TIMEFRAMES.find(t => t.id === tf).label;
  label.title = restriction || '시간 간격';
  const note = $('#index-interval-note');
  note.textContent = restriction;
  note.hidden = !restriction;
  document.querySelectorAll('#tfs button').forEach(b => {
    b.classList.toggle('on', b.dataset.tf === tf);
    b.disabled = Boolean(restriction) && isMinuteTf(b.dataset.tf);
    b.title = b.disabled ? restriction : '';
  });
  chart.applyOptions({ timeScale: { timeVisible: isMinuteTf(tf) } });
}

async function switchTf(tf) {
  if (isMinuteTf(tf) && paperIndexRestriction()) return;
  replay.stop(false);
  stopQuotes();
  setLive('connecting', '시세를 불러오는 중입니다.');
  localStorage.setItem('kis.tf', tf);
  applyTimeframe(tf);
  const loading = loadCandles();
  const seq = state.loadSeq;
  await loading;
  if (state.loadSeq === seq) followQuotes();
}

function updateSourceBadge() {
  const cfg = state.config;
  const badge = $('#source');
  if (usesYahooQuotes(state.code)) {
    badge.textContent = 'Yahoo Finance';
    badge.title = 'Yahoo Finance에서 받은 시장 시세입니다. 시세 지연 시간은 거래소마다 다릅니다.';
    badge.dataset.kind = 'yahoo';
    return;
  }
  if (!cfg) return;
  const market = Array.from($('#domestic-market').options).find(option => option.value === cfg.market);
  const venue = isIndex(state.code) ? ` · ${indexLabel(state.code)}`
    : DOMESTIC_RE.test(state.code) ? ` · ${market.label}` : '';
  badge.textContent = cfg.source === 'kis' ? `KIS ${cfg.env === 'real' ? '실전' : '모의'}${venue}` : '데모 · 가상 시세';
  badge.title = paperIndexRestriction();
  badge.dataset.kind = cfg.source;
}

function setupSettings() {
  const fitPrice = $('#fit-price-on-symbol');
  fitPrice.checked = localStorage.getItem('kis.fitPriceOnSymbol') !== 'false';
  fitPrice.onchange = () => localStorage.setItem('kis.fitPriceOnSymbol', String(fitPrice.checked));
  const tablist = $('#view-tabs');
  const tabs = [...tablist.querySelectorAll('[role="tab"]')];
  for (const tab of tabs) {
    tab.addEventListener('click', () => {
      for (const item of tabs) {
        const active = item === tab;
        const panel = document.getElementById(item.getAttribute('aria-controls'));
        item.setAttribute('aria-selected', String(active));
        item.tabIndex = active ? 0 : -1;
        panel.hidden = !active;
        if (active && tablist.hidden) panel.focus({ preventScroll: true });
      }
    });
    tab.addEventListener('keydown', (event) => {
      let next;
      if (event.key === 'Home') next = tabs[0];
      else if (event.key === 'End') next = tabs[tabs.length - 1];
      else if (['ArrowLeft', 'ArrowRight'].includes(event.key)) next = tabs.find(item => item !== tab);
      else return;
      event.preventDefault();
      next.click();
      next.focus();
    });
  }
  const select = $('#domestic-market');
  const status = $('#market-status');
  select.addEventListener('change', async () => {
    select.disabled = true;
    status.dataset.kind = '';
    status.textContent = '저장 중…';
    try {
      const { market } = await api('/api/settings/market', {
        method: 'PUT', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ market: select.value }),
      });
      state.config.market = market;
      updateSourceBadge();
      status.textContent = '저장됨';
      if (state.config.source === 'kis' && DOMESTIC_RE.test(state.code)) {
        replay.stop(false);
        paintLive();
        const code = state.code, tf = state.tf;
        await loadCandles({ keepRange: true });
        if (state.code === code && state.tf === tf) followQuotes();
      }
    } catch (error) {
      select.value = state.config.market;
      status.dataset.kind = 'error';
      status.textContent = error.message;
    } finally {
      select.disabled = false;
    }
  });
}

async function init() {
  try {
    const saved = JSON.parse(localStorage.getItem(savedYahooKey) || 'null');
    if (saved?.code === state.code && isYahoo(saved.code) && typeof saved.name === 'string') learnNames([saved]);
  } catch { /* 저장된 메타데이터가 없어도 서버에서 다시 조회합니다. */ }
  $('#show-license').addEventListener('click', () => $('#license').showModal());
  setupDateJump();
  $('#history-retry').addEventListener('click', () => { older.error = ''; loadOlderBars(); });
  $('#index-daily').addEventListener('click', () => switchTf('D'));
  buildToolbar();
  setupSettings();
  try {
    state.config = await api('/api/config');
    if (state.config.drawing_sync_role) setupDrawingSync(state.config.drawing_sync_role, api,
      () => drawingStore.refresh(() => !drawings.draft && !drawings.drag && !drawings.brushing));
    state.quoteTransport = state.config.quote_transport;
    $('#view-tabs').hidden = !!state.config.desktop;
    $('#domestic-market').value = state.config.market;
    $('#domestic-market').disabled = state.config.source === 'yahoo';
    if (state.config.source === 'yahoo') $('#market-status').textContent = '거래소 선택은 한투 시세에 적용됩니다.';
  } catch (e) {
    $('#market-status').textContent = '거래소 설정을 불러오지 못했습니다.';
  }
  await switchCode(parseCode(state.code) || '005930');
}

window.__app = { state, chart, candles, drawings, timeMap, chartTypes, studies, indicatorLibrary, replay };
init();
