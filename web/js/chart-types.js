import { makePriceSeries, pricePoint, lineBreakData, nextBreak } from './chart-types-renderer.js';
import { isPricePattern, patternBoxSize, pricePatternData, pricePatternView } from './price-patterns.js';
import { createSessionProfiles, sessionIntervalMessage } from './session-profiles.js';

const STORAGE = 'kis.chartType.v1';
const sessionType = id => id === 'tpo' || id === 'session-profile';
const groups = [
  [
    ['bars', '바', 'M5 3v16M2 9h3m0 4h3M14 5v17m-3-8h3m0-5h3'],
    ['candles', '캔들', 'M6 2v4m0 10v5M3.5 6h5v10h-5ZM15 4v5m0 8v5m-2.5-13h5v8h-5Z'],
    ['hollow', '할로우캔들', 'M6 2v4m0 10v5M3.5 6h5v10h-5ZM15 4v5m0 8v5m-2.5-13h5v8h-5Zm1.7 2v4m1.6-4v4'],
    ['volume-candles', '볼륨 캔들', 'M5 2v4m0 10v5M3.5 6h3v10h-3ZM15 4v5m0 8v5m-4-13h8v8h-8Z'],
  ],
  [
    ['line', '라인', 'm1 18 7-8 4 3 7-9'],
    ['markers', '마커가 있는 라인', 'm3 17 5-6 4 2 6-7M2 16h2v2H2Zm5-6h2v2H7Zm4 2h2v2h-2Zm6-7h2v2h-2Z'],
    ['step', '스텝 라인', 'M1 18h6v-5h5V8h7V3'],
  ],
  [
    ['area', '영역', 'm1 17 6-7 5 2 7-9M1 20h18M4 17v2m4-5v5m4-4v4m4-9v9'],
    ['hlc-area', 'HLC 영역', 'm1 16 6-8 5 2 7-7M1 19l6-7 5 2 7-7M3 20l5-4 5 1 6-6'],
    ['baseline', '베이스라인', 'M1 12h2m3 0h2m4 0h2m3 0h2M2 18l5-12 5 10 6-13'],
  ],
  [
    ['columns', '컬럼', 'M2 13h4v7H2Zm6-10h4v17H8Zm6 5h4v12h-4Z'],
    ['high-low', '하이-로우', 'M3 3h5v16H3Zm10 5h5v14h-5Z'],
  ],
  [
    ['footprint', '볼륨 풋프린트', 'M1 4h18M3 8h6m3 0h6M3 12h3m6 0h7M3 16h7m2 0h4M1 20h18', '가격대별 매수·매도 거래량이 필요합니다. 현재 한투 시세는 봉별 총거래량을 제공합니다.'],
    ['tpo', '시간 가격 기회', 'M2 3h3v3H2Zm0 5h3v3H2Zm5 0h3v3H7Zm-5 5h3v3H2Zm5 0h3v3H7Zm5 0h3v3h-3Zm-10 5h3v3H2Zm5 0h3v3H7Zm5 0h3v3h-3Zm5 0h3v3h-3Z'],
    ['session-profile', '세션 볼륨 프로파일', 'M2 2v19h17M3 5h7v3H3m0 2h13v3H3m0 2h9v3H3'],
  ],
  [
    ['heikin-ashi', '하이킨 아시', 'M5 3v4m0 7v5M2 7h6v7H2Zm12-1v4m0 8v4m-3-12h6v8h-6Z'],
    ['renko', '렌코', 'M1 15h6v6H1Zm6-6h6v6H7Zm6-6h6v6h-6Z'],
    ['line-break', '라인브레이크', 'M2 12h5v9H2Zm6-6h5v7H8Zm6 0h5v12h-5Z'],
    ['kagi', '카기', 'M1 19V8h4v7h4V4h5v16h5v-9'],
    ['pnf', '포인트앤피겨', 'M2 3l4 4m0-4L2 7m8 2 4 4m0-4-4 4m-8 2 4 4m0-4-4 4M11 3h2v3h-2Zm5 12h3v4h-3Z'],
    ['range-bars', '레인지', 'M2 6v11m-2-6h4m5-7v16m-3-6h6m5-4v12m-2-5h4', '체결 순서로 가격 변화 폭을 묶을 수 있는 틱 시세가 필요합니다.'],
  ],
];

const entries = new Map(groups.flat().map(entry => [entry[0], entry]));
function icon(id) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 21 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', entries.get(id)[2]);
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'currentColor');
  path.setAttribute('stroke-width', '1.05');
  path.setAttribute('stroke-linejoin', 'round');
  path.setAttribute('stroke-linecap', 'round');
  svg.append(path);
  return svg;
}

function typeMenu(mount, select, reasonFor, switchInterval, intradayRestriction) {
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.id = 'chart-type-toggle';
  toggle.setAttribute('aria-haspopup', 'menu');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'chart-type-menu');
  const caption = document.createElement('span');
  caption.className = 'chart-type-caption';
  mount.append(toggle, caption);

  const menu = document.createElement('div');
  menu.id = 'chart-type-menu';
  menu.className = 'chart-type-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', '차트 유형');
  const message = document.createElement('p');
  message.className = 'chart-type-feedback';
  message.setAttribute('role', 'status');
  const buttons = [];
  for (const group of groups) {
    if (buttons.length) menu.append(document.createElement('hr'));
    for (const [id, label] of group) {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.chartType = id;
      b.setAttribute('role', 'menuitemradio');
      b.tabIndex = -1;
      b.append(icon(id), label);
      b.onclick = event => {
        const reason = reasonFor(id);
        if (reason) {
          message.textContent = reason;
          if (sessionType(id) && switchInterval && !intradayRestriction()) {
            const go = document.createElement('button');
            go.type = 'button';
            go.textContent = '5분봉으로 열기';
            go.onclick = () => { close(true); switchInterval(id); };
            message.append(go);
          }
          b.after(message);
          message.scrollIntoView({ block: 'nearest' });
          if (event.detail === 0) message.querySelector('button')?.focus({ preventScroll: true });
          return;
        }
        select(id);
        close(true);
      };
      menu.append(b);
      buttons.push(b);
    }
  }
  document.body.append(menu);

  function close(restoreFocus = false) {
    menu.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    message.remove();
    if (restoreFocus) toggle.focus({ preventScroll: true });
  }
  function open() {
    const rect = toggle.getBoundingClientRect();
    availability();
    menu.hidden = false;
    menu.style.left = Math.max(4, Math.min(window.innerWidth - menu.offsetWidth - 4, rect.left)) + 'px';
    menu.style.top = (rect.bottom + 5) + 'px';
    menu.style.maxHeight = Math.max(80, window.innerHeight - rect.bottom - 12) + 'px';
    toggle.setAttribute('aria-expanded', 'true');
    const current = buttons.find(b => b.getAttribute('aria-checked') === 'true');
    current?.focus({ preventScroll: true });
    current?.scrollIntoView({ block: 'nearest' });
  }
  toggle.onclick = () => menu.hidden ? open() : close(true);
  toggle.onkeydown = event => {
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); open(); }
  };
  menu.onkeydown = event => {
    event.stopPropagation();
    const i = buttons.indexOf(document.activeElement);
    if (event.key === 'Tab') { close(true); return; }
    if (event.key === 'Escape') { event.preventDefault(); close(true); return; }
    let index = i;
    if (event.key === 'ArrowDown') index = (i + 1) % buttons.length;
    else if (event.key === 'ArrowUp') index = (i - 1 + buttons.length) % buttons.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = buttons.length - 1;
    else return;
    event.preventDefault();
    buttons[index].focus();
  };
  document.addEventListener('pointerdown', e => {
    if (!menu.hidden && !mount.contains(e.target) && !menu.contains(e.target)) close();
  });
  document.getElementById('settings-tab')?.addEventListener('click', () => close());
  window.addEventListener('resize', () => close());
  function availability() {
    for (const b of buttons) {
      const id = b.dataset.chartType;
      const reason = reasonFor(id);
      if (reason) {
        b.setAttribute('aria-disabled', 'true');
        b.title = reason;
      } else {
        b.removeAttribute('aria-disabled');
        b.title = id === 'line-break' ? '종가 기준 3선 전환을 원래 봉의 시각에 표시합니다.' : '';
      }
    }
  }
  return (id, detail = '') => {
    mount.dataset.mode = id;
    caption.textContent = detail;
    toggle.replaceChildren(icon(id));
    toggle.title = '차트 유형 · ' + entries.get(id)[1] + (detail ? ' (' + detail + ')' : '');
    toggle.setAttribute('aria-label', toggle.title);
    for (const b of buttons) b.setAttribute('aria-checked', String(b.dataset.chartType === id));
    availability();
  };
}

export function setupChartTypes({ chart, candles, getBars, mount, getTimeframe = () => 'D', setTimeframe, intradayRestriction = () => '' }) {
  let mode = 'candles';
  let series = null;
  let derived = [];
  let sourceCount = 0;
  let pricePrecision;
  let lastBase;
  let lastBox;
  let profiles;
  const initial = candles.options();
  const reasonFor = id => sessionType(id) ? intradayRestriction() || sessionIntervalMessage(id, getTimeframe()) : (entries.get(id)?.[3] || '');
  const switchInterval = setTimeframe && (async id => {
    const initialMode = mode;
    await setTimeframe('5');
    if (mode === initialMode && String(getTimeframe()) === '5') select(id);
  });
  const refreshMenu = typeMenu(mount, select, reasonFor, switchInterval, intradayRestriction);
  const visible = {
    upColor: initial.upColor, downColor: initial.downColor,
    wickUpColor: initial.wickUpColor, wickDownColor: initial.wickDownColor,
    wickVisible: initial.wickVisible, borderVisible: initial.borderVisible, priceLineVisible: initial.priceLineVisible,
    lastValueVisible: initial.lastValueVisible, autoscaleInfoProvider: initial.autoscaleInfoProvider || (get => get()),
  };
  const transparent = { upColor: 'transparent', downColor: 'transparent', wickVisible: false, borderVisible: false,
    priceLineVisible: false, lastValueVisible: false, autoscaleInfoProvider: () => null };

  function baseline() {
    if (mode !== 'baseline' || !series) return;
    const bars = getBars();
    if (!bars.length) return;
    const range = chart.timeScale().getVisibleLogicalRange();
    const from = Math.max(0, Math.min(bars.length - 1, Math.floor(range?.from ?? 0)));
    const to = Math.min(bars.length, Math.max(from + 1, Math.ceil(range?.to ?? bars.length) + 1));
    let low = Infinity, high = -Infinity;
    for (let i = from; i < to; i++) {
      low = Math.min(low, bars[i].close);
      high = Math.max(high, bars[i].close);
    }
    const price = (low + high) / 2;
    if (price !== lastBase) {
      lastBase = price;
      series.applyOptions({ baseValue: { type: 'price', price } });
    }
  }

  function refresh(scope = 'full') {
    if (sessionType(mode)) {
      if (reasonFor(mode)) { select('candles'); return; }
      profiles.setData(mode, getBars(), getTimeframe());
      return;
    }
    if (!series) return;
    const bars = getBars();
    const format = candles.options().priceFormat;
    if (format.precision !== pricePrecision) {
      pricePrecision = format.precision;
      series.applyOptions({ priceFormat: format });
    }
    const i = bars.length - 1;
    const incremental = scope === 'update' && i >= 0 && (sourceCount === bars.length || sourceCount === i);
    if (isPricePattern(mode)) {
      const box = patternBoxSize(bars, format.minMove);
      series.setData(pricePatternData(mode, bars, box));
      if (box !== lastBox) {
        lastBox = box;
        refreshMenu(mode, patternCaption());
      }
    } else if (mode === 'line-break') {
      if (incremental) {
        if (derived.at(-1)?.time === bars[i].time) derived.pop();
        const next = i > 0 ? nextBreak(derived, bars[i], bars[0].close) : null;
        if (next) derived.push(next);
        series.update(next || { time: bars[i].time });
      } else {
        derived = lineBreakData(bars);
        series.setData(derived);
      }
    } else if (incremental) {
      const item = pricePoint(mode, bars[i], bars[i - 1], derived[i - 1]);
      derived[i] = item;
      series.update(item);
    } else {
      derived = [];
      for (let j = 0; j < bars.length; j++) derived.push(pricePoint(mode, bars[j], bars[j - 1], derived[j - 1]));
      series.setData(derived);
    }
    sourceCount = bars.length;
    baseline();
  }

  function patternCaption() {
    const unit = mode === 'pnf' ? ' · 3칸 반전' : '';
    return '종가 · 폭 ' + String(lastBox) + unit + ' · 원봉 시각';
  }

  function select(id) {
    const entry = entries.get(id);
    if (!entry || reasonFor(id)) return false;
    if (id !== mode) {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (series) chart.removeSeries(series);
      profiles?.clear();
      mode = id;
      sourceCount = 0;
      derived = [];
      lastBase = undefined;
      lastBox = undefined;
      pricePrecision = undefined;
      const format = candles.options().priceFormat;
      series = null;
      if (isPricePattern(mode)) {
        series = chart.addCustomSeries(pricePatternView(mode), { priceFormat: format, priceScaleId: 'right' });
      } else if (sessionType(mode)) {
        profiles ||= createSessionProfiles({ chart, series: candles });
      } else if (mode !== 'candles') {
        series = makePriceSeries(chart, mode, format);
      }
      if (sessionType(mode)) {
        const shade = mode === 'tpo' ? '#64738b66' : 'transparent';
        candles.applyOptions({ ...visible, upColor: shade, downColor: shade, wickUpColor: shade, wickDownColor: shade });
      } else candles.applyOptions(series ? transparent : visible);
      if (series && typeof series.setSeriesOrder === 'function') series.setSeriesOrder(1);
      refresh('full');
      if (range && getBars().length) chart.timeScale().setVisibleLogicalRange(range);
    }
    const detail = isPricePattern(mode) ? patternCaption() : mode === 'line-break' ? '종가 3선 · 원봉 시각' : '';
    refreshMenu(mode, detail);
    try { localStorage.setItem(STORAGE, mode); } catch { /* The mode also works for this session. */ }
    return true;
  }
  candles.subscribeDataChanged(refresh);
  chart.timeScale().subscribeVisibleLogicalRangeChange(baseline);
  let saved = null;
  try { saved = localStorage.getItem(STORAGE); } catch { /* Default to candles. */ }
  select(entries.has(saved) && !reasonFor(saved) ? saved : 'candles');
  return { select, refresh, get mode() { return mode; }, get series() { return series || candles; } };
}
