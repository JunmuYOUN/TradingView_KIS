import { studyValues, studyTailBars, vwapAnchor } from './study-data.js';
import { editStudySettings, getStudySettings } from './indicator-settings.js';
import { readPersonalScripts } from './script-store.js';
import { compilePersonalScript } from './study-expression.js';
import { createIchimokuCloud } from './ichimoku-cloud.js';

const line = (color, label) => ({ color, label });
const money = { color: '#26a69a', label: '거래대금', histogram: true };
const bars = { color: '#26a69a', label: '히스토그램', histogram: true, signed: true };

function study(id, name, english, short, description, lines, overlay = false, guides = []) {
  return { id, name, english, short, description, lines, overlay, guides, category: 'technical' };
}
export const INDICATORS = [
  study('volume24h', '24-hour volume', '24-hour volume · 24시간 거래량 · 거래대금', '24h Vol',
    '분봉·일봉의 최근 24시간 거래대금 추정치입니다. 지수·선물·환율은 봉별 거래량을 합산합니다.', [money]),
  study('rsi', 'Relative strength index', 'Relative strength index · 상대강도지수', 'RSI 14',
    '종가 상승분과 하락분을 14봉 와일더 평균으로 계산한 상대강도지수', [line('#aa86ec', 'RSI')], false, [30, 70]),
  study('vwap', '거래량 가중 평균 가격', 'Volume weighted average price · VWAP', 'VWAP',
    '대표가격(HLC3)을 거래량으로 가중합니다. 분봉은 거래일, 일봉은 월, 주·월봉은 연도마다 누적을 시작합니다.', [line('#3c9aff', 'VWAP')], true),
  study('sma', '단순 이동평균', 'Simple moving average · MA · SMA', 'SMA 20',
    '최근 20봉 종가의 산술평균', [line('#f6b440', 'SMA')], true),
  study('ema', '지수 이동평균', 'Exponential moving average · EMA', 'EMA 9',
    '최근 가격에 더 큰 비중을 주는 9봉 지수이동평균', [line('#42b2db', 'EMA')], true),
  study('wma', '가중 이동평균', 'Weighted moving average · WMA', 'WMA 9',
    '최근 9봉 종가에 1부터 9까지 가중치를 적용한 평균', [line('#e08aab', 'WMA')], true),
  study('bb', '볼린저 밴드', 'Bollinger Bands · BB', 'BB 20 2', '20봉 평균과 위·아래 두 표준편차',
    [line('#ffbd4a', '중앙'), line('#5691fb', '상단'), line('#5691fb', '하단')], true),
  study('ichimoku', '일목균형표', 'Ichimoku Cloud · Ichimoku Kinko Hyo · 일목구름 · 구름대', '일목균형표',
    '전환선과 기준선, 앞으로 이동한 두 선행스팬 사이의 구름대, 뒤로 이동한 종가를 표시합니다.',
    [line('#448aff', '전환선'), line('#ef5350', '기준선'), line('#26a69a', '선행스팬 A'),
      line('#e57373', '선행스팬 B'), line('#9ccc65', '후행스팬')], true),
  study('macd', '이동평균 수렴·확산', 'Moving average convergence divergence · MACD', 'MACD 12 26 9',
    '12봉·26봉 EMA의 차이, 9봉 신호선과 히스토그램', [bars, line('#448aff', 'MACD'), line('#ffab40', '신호')], false, [0]),
  study('stoch', '스토캐스틱', 'Stochastic oscillator · %K · %D', 'Stoch 14 3 3',
    '14봉 고저 범위 내 종가의 위치와 3봉 평활 %K·%D', [line('#478dff', '%K'), line('#f4a43b', '%D')], false, [20, 80]),
  study('atr', '평균 실제 범위', 'Average true range · ATR', 'ATR 14',
    '이전 종가와 현재 고저를 반영한 변동폭의 14봉 와일더 평균', [line('#df777c', 'ATR')]),
  study('donchian', '돈치안 채널', 'Donchian channels · DC', 'DC 20', '최근 20봉 최고가, 최저가와 중앙선',
    [line('#39a5ed', '상단'), line('#eca656', '중앙'), line('#39a5ed', '하단')], true),
  study('obv', '누적 거래량 지표', 'On balance volume · OBV', 'OBV',
    '종가 상승일의 거래량을 더하고 하락일의 거래량을 뺀 누적값', [line('#42a5a1', 'OBV')]),
  study('roc', '가격 변화율', 'Rate of change · ROC', 'ROC 9',
    '9봉 전 종가 대비 현재 종가의 백분율 변화', [line('#3ba5ed', 'ROC')], false, [0]),
  study('momentum', '모멘텀', 'Momentum · MOM', 'Mom 10',
    '현재 종가와 10봉 전 종가의 차이', [line('#bb7bdf', '모멘텀')], false, [0]),
];
const storageKey = 'kis.activeStudies.v1';
const number = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 });
const smallPrice = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 4 });
const compact = new Intl.NumberFormat('ko-KR', { notation: 'compact', maximumFractionDigits: 2 });

function loadSelection(catalog) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return Array.isArray(saved) ? [...new Set(saved)].filter(id => catalog.has(id)) : [];
  } catch { return []; }
}

export function createIndicators({ chart, container, openStudyAlert, onStudyAlertReset }) {
  const LC = window.LightweightCharts;
  const active = new Map();
  const catalog = new Map(INDICATORS.map(item => [item.id, item]));
  const listeners = new Set();
  const host = container.parentElement;
  const overlay = document.createElement('div');
  overlay.className = 'study-overlay-legends';
  host.append(overlay);
  let source = [];
  let context = {};
  let priceFormatter;
  let hover = null;
  let hoverSlot = null;
  let frame = 0;
  const observedPanes = new Set();
  const observer = new ResizeObserver(scheduleLayout);
  observer.observe(container);
  observer.observe(overlay);

  function scheduleLayout() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      host.style.setProperty('--overlay-study-height', overlay.offsetHeight + 'px');
      const top = host.getBoundingClientRect().top;
      const seen = new Set();
      for (const item of active.values()) {
        if (item.meta.overlay) continue;
        const element = item.series[0].getPane().getHTMLElement();
        if (!element) continue;
        seen.add(element);
        item.legend.style.top = Math.max(0, element.getBoundingClientRect().top - top + 4) + 'px';
        if (!observedPanes.has(element)) {
          observedPanes.add(element);
          observer.observe(element);
        }
      }
      for (const element of observedPanes) {
        if (!seen.has(element)) { observer.unobserve(element); observedPanes.delete(element); }
      }
    });
  }
  chart.timeScale().subscribeSizeChange(scheduleLayout);

  function format(item, value) {
    if (!Number.isFinite(value)) return '—';
    if (item.meta.id === 'volume24h' || item.meta.id === 'obv') return compact.format(value);
    if (priceStudy(item) && priceFormatter) return priceFormatter.format(value);
    return item.meta.overlay && Math.abs(value) < 1 ? smallPrice.format(value) : number.format(value);
  }

  function priceStudy(item) {
    return item.meta.overlay || ['atr', 'momentum', 'macd'].includes(item.meta.id);
  }

  function alertValue(id, index, slot = source.length - 1) {
    const item = active.get(id);
    if (!item) return null;
    const value = item.rawValues[index]?.[slot - (item.offsets?.[index] || 0)];
    return Number.isFinite(value) ? value : null;
  }

  function alertTarget(id) {
    const item = active.get(id);
    if (!item) return null;
    const { meta } = item;
    const parameters = Object.values(item.settings.parameters);
    return {
      id, name: meta.name + (parameters.length ? ' (' + parameters.join(', ') + ')' : ''), overlay: meta.overlay,
      // Ichimoku's lagging span ends in the past, so it cannot be crossed at the latest candle.
      lines: meta.lines.flatMap((line, index) => item.offsets?.[index] < 0 ? [] :
        [{ index, label: line.label, value: alertValue(id, index) }]),
    };
  }

  function updateLegend(item) {
    const { meta } = item;
    let label = meta.short;
    const parameters = Object.values(item.settings.parameters);
    if (parameters.length) label = meta.short.split(' ')[0] + ' ' + parameters.join(' ');
    if (item.cloud) {
      const { conversion, base, spanB } = item.settings.parameters;
      const required = Math.max(conversion, base, spanB);
      if (source.length < required) label += ` · 구름대 ${source.length}/${required}봉`;
    }
    if (meta.id === 'vwap') label += ' · ' + vwapAnchor(context.tf);
    if (meta.id === 'volume24h') {
      if (context.isIndex || context.rawVolume) label += ' · 거래량';
      else label += context.code?.includes(':') ? ' · USD 추정' : ' · 원 추정';
    }
    item.name.textContent = label;
    item.values.forEach((span, i) => {
      if (item.cloud) {
        const slot = hoverSlot ?? source.length - 1;
        const value = item.rawValues[i][slot - item.offsets[i]];
        span.textContent = meta.lines[i].label + ' ' + format(item, value);
        return;
      }
      const value = hover ? item.rawValues[i]?.[hoverSlot] : item.last[i];
      span.textContent = meta.id === 'volume24h' && ['W', 'M'].includes(context.tf)
        ? '분·일봉에서 표시' : format(item, value);
    });
  }

  function draw(item, tailOnly = false) {
    const data = tailOnly ? studyTailBars(item.meta.id, source, item.settings.parameters) : source;
    const calculated = item.meta.compiled ? [item.meta.compiled.evaluate(data)]
      : studyValues(item.meta.id, data, context.tf, item.settings.parameters, context.isIndex || context.rawVolume);
    if (tailOnly) {
      calculated.forEach((values, i) => { item.rawValues[i][source.length - 1] = values.at(-1); });
    } else item.rawValues = calculated;
    const result = item.rawValues;
    if (item.cloud) {
      const { displacement } = item.settings.parameters;
      const { colors } = item.settings;
      item.offsets = [0, 0, displacement, displacement, -displacement];
      item.cloud.setData(result[2], result[3], displacement, colors[2], colors[3]);
    }
    const precision = context.pricePrecision ?? 2;
    if (priceStudy(item) && (item.precision ?? 2) !== precision) {
      for (const series of item.series) series.applyOptions({ priceFormat: { type: 'price', precision, minMove: 1 / 10 ** precision } });
      item.precision = precision;
    }
    item.series.forEach((series, i) => {
      const values = result[i];
      const offset = item.offsets?.[i] || 0;
      const points = [];
      // The native plot uses real candle times. The primitive handles projected slots beyond the last candle.
      const end = Math.min(values.length, source.length - offset);
      const start = Math.max(0, -offset, tailOnly ? end - 1 : 0);
      for (let j = start; j < end; j++) {
        const value = values[j];
        if (!Number.isFinite(value)) {
          if (tailOnly) points.push({ time: source[j + offset].time });
          continue;
        }
        const point = { time: source[j + offset].time, value };
        if (item.meta.lines[i].signed) point.color = value >= 0 ? item.settings.colors[i] : item.settings.falling;
        points.push(point);
      }
      if (!tailOnly) series.setData(points);
      else if (points.length) series.update(points[0]);
      item.last[i] = values.at(-1);
    });
    updateLegend(item);
  }

  function notify() {
    const ids = [...active.keys()];
    try { localStorage.setItem(storageKey, JSON.stringify(ids)); } catch { /* Keep the running chart usable. */ }
    for (const listener of listeners) listener(ids);
  }

  function mount(id) {
    const meta = catalog.get(id);
    if (!meta || active.has(id)) return false;
    const settings = meta.compiled ? { parameters: {}, colors: [meta.lines[0].color] } : getStudySettings(meta);
    const pane = meta.overlay ? 0 : chart.panes().length;
    const series = meta.lines.map((channel, i) => {
      const options = {
        color: settings.colors[i], priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        priceFormat: ['volume24h', 'obv'].includes(id) ? { type: 'volume' } : { type: 'price', precision: 2, minMove: 0.01 },
      };
      if (!channel.histogram) options.lineWidth = 2;
      return chart.addSeries(channel.histogram ? LC.HistogramSeries : LC.LineSeries, options, pane);
    });
    if (!meta.overlay) {
      const paneApi = series[0].getPane();
      paneApi.setStretchFactor(chart.panes()[0].getStretchFactor() * 0.34);
      series[0].priceScale().applyOptions({ scaleMargins: { top: 0.24, bottom: 0.12 } });
    }
    for (const level of meta.guides) {
      series[0].createPriceLine({ price: level, color: '#73778288', lineWidth: 1,
        lineStyle: LC.LineStyle.Dashed, axisLabelVisible: false, title: '' });
    }

    const legend = document.createElement('div');
    legend.className = 'study-legend' + (meta.overlay ? '' : ' study-pane-legend');
    legend.dataset.study = id;
    legend.title = meta.description;
    const name = document.createElement('span');
    name.className = 'study-name';
    legend.append(name);
    const values = meta.lines.map((channel, i) => {
      const span = document.createElement('span');
      span.className = 'study-value';
      span.style.color = settings.colors[i];
      span.title = channel.label;
      legend.append(span);
      return span;
    });
    const gear = document.createElement('button');
    gear.type = 'button';
    gear.className = 'study-settings';
    gear.title = meta.name + ' 설정';
    gear.setAttribute('aria-label', gear.title);
    gear.textContent = '⚙';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'study-remove';
    close.setAttribute('aria-label', meta.name + ' 삭제');
    close.title = meta.name + ' 삭제';
    close.textContent = '×';
    close.onclick = () => remove(id);
    legend.append(gear, close);
    (meta.overlay ? overlay : host).append(legend);

    const item = { meta, series, values, name, legend, settings, last: [] };
    if (id === 'ichimoku') {
      item.cloud = createIchimokuCloud(chart, series[0], chart.panes()[0].getSeries()[0]);
    }
    gear.onclick = () => {
      const openAlert = openStudyAlert && (() => openStudyAlert(alertTarget(id)));
      if (meta.compiled) {
        window.dispatchEvent(new CustomEvent('kis-edit-personal-script', { detail: { id, openAlert } }));
        return;
      }
      editStudySettings(meta, item.settings, next => {
        const previous = item.settings.parameters;
        item.settings = next;
        series.forEach((plot, i) => {
          plot.applyOptions({ color: next.colors[i] });
          values[i].style.color = next.colors[i];
        });
        draw(item);
        if (Object.keys(previous).some(key => previous[key] !== next.parameters[key])) onStudyAlertReset?.(id);
        if (item.cloud && previous.displacement !== next.parameters.displacement) notify();
      }, openAlert);
    };
    active.set(id, item);
    draw(item);
    scheduleLayout();
    return true;
  }
  function unmount(id) {
    const item = active.get(id);
    if (!item) return false;
    active.delete(id);
    item.legend.remove();
    item.cloud?.destroy();
    for (const series of item.series) chart.removeSeries(series);
    scheduleLayout();
    return true;
  }
  function remove(id) {
    if (!unmount(id)) return false;
    onStudyAlertReset?.(id, 'remove');
    notify();
    return true;
  }

  function setPersonalScripts(entries) {
    const known = new Set();
    let changedActive = false;
    for (const script of entries) {
      const { id, name, expression, overlay, color } = script;
      if (typeof id !== 'string' || !id.startsWith('local:') || known.has(id)) continue;
      const old = catalog.get(id);
      if (old?.name === name && old.expression === expression && old.overlay === overlay && old.lines[0].color === color) {
        known.add(id);
        continue;
      }
      let compiled;
      try { compiled = old?.expression === expression ? old.compiled : compilePersonalScript(expression); }
      catch { continue; }
      known.add(id);
      const wasActive = unmount(id);
      const meta = study(id, name, name, name, expression, [line(color, name)], overlay);
      catalog.set(id, { ...meta, expression, compiled });
      if (wasActive) {
        mount(id);
        if (old.overlay !== overlay) onStudyAlertReset?.(id, 'remove');
        else if (old.expression !== expression) onStudyAlertReset?.(id);
        changedActive = true;
      }
    }
    for (const [id, meta] of catalog) {
      if (meta.compiled && !known.has(id)) {
        if (unmount(id)) changedActive = true;
        catalog.delete(id);
        onStudyAlertReset?.(id, 'remove');
      }
    }
    if (changedActive) notify();
  }

  chart.subscribeCrosshairMove(event => {
    hover = event.time == null ? null : event.seriesData;
    const logical = event.point ? chart.timeScale().coordinateToLogical(event.point.x) : null;
    hoverSlot = Number.isFinite(logical) ? Math.round(logical) : null;
    for (const item of active.values()) updateLegend(item);
  });
  setPersonalScripts(readPersonalScripts());
  for (const id of loadSelection(catalog)) mount(id);
  return {
    setData(data, nextContext, tailOnly = false) {
      source = data;
      if (nextContext?.pricePrecision !== context.pricePrecision) {
        const precision = nextContext?.pricePrecision;
        priceFormatter = precision == null ? null : new Intl.NumberFormat('ko-KR', {
          minimumFractionDigits: precision, maximumFractionDigits: precision,
        });
      }
      context = nextContext || {};
      if (!tailOnly) {
        hover = null;
        hoverSlot = null;
      }
      for (const item of active.values()) draw(item, tailOnly);
    },
    add(id) { if (!mount(id)) return false; notify(); return true; },
    alertTarget,
    alertValue,
    setPersonalScripts,
    remove,
    get projectionBars() { return active.get('ichimoku')?.settings.parameters.displacement || 0; },
    has: id => active.has(id),
    getActive: () => [...active.keys()],
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
  };
}
