// Profiles share the chart's original time and price scales, including empty periods between sessions.
const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';

export function sessionIntervalMessage(mode, tf) {
  if (mode === 'tpo' && !['1', '3', '5', '15', '30'].includes(String(tf))) {
    return '시간 가격 기회는 30분 이하 분봉에서 표시됩니다.';
  }
  if (mode === 'session-profile' && !/^\d+$/.test(String(tf))) {
    return '세션 볼륨 프로파일은 분봉에서 표시됩니다.';
  }
  return '';
}

function automaticStep(range) {
  if (range === 0) return 0;
  const target = range / 24;
  const unit = 10 ** Math.floor(Math.log10(target));
  const fraction = target / unit;
  const factor = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return factor * unit;
}

function oneSession(segment, mode) {
  let low = Infinity, high = -Infinity;
  for (const bar of segment) {
    low = Math.min(low, bar.low);
    high = Math.max(high, bar.high);
  }
  const step = automaticStep(high - low);
  const length = step > 0 ? Math.max(1, Math.ceil((high - low) / step - 1e-10)) : 1;
  const rows = Array.from({ length }, (_, i) => ({
    low: low + step * i, high: Math.min(high, low + step * (i + 1)), weight: 0, blocks: [],
  }));
  const bin = price => step > 0 ? Math.max(0, Math.min(length - 1, Math.floor((price - low) / step))) : 0;

  if (mode === 'tpo') {
    const periods = new Map();
    const first = Math.floor(segment[0].time / 1800);
    for (const bar of segment) {
      const index = Math.floor(bar.time / 1800) - first;
      const block = periods.get(index);
      if (!block) periods.set(index, { low: bar.low, high: bar.high });
      else { block.low = Math.min(block.low, bar.low); block.high = Math.max(block.high, bar.high); }
    }
    for (const [index, block] of periods) {
      for (let i = bin(block.low); i <= bin(block.high); i++) {
        rows[i].blocks.push(index);
        rows[i].weight++;
      }
    }
  } else {
    for (const bar of segment) {
      if (!(bar.volume > 0)) continue;
      const from = bin(bar.low), to = bin(bar.high);
      if (from === to) { rows[from].weight += bar.volume; continue; }
      // With OHLCV only, allocate each bar's volume by its overlap with each price bin.
      for (let i = from; i <= to; i++) {
        const overlap = Math.max(0, Math.min(bar.high, rows[i].high) - Math.max(bar.low, rows[i].low));
        rows[i].weight += bar.volume * overlap / (bar.high - bar.low);
      }
    }
  }
  let poc = 0, total = 0;
  const middle = (length - 1) / 2;
  for (let i = 0; i < length; i++) {
    total += rows[i].weight;
    if (rows[i].weight > rows[poc].weight ||
        (rows[i].weight === rows[poc].weight && Math.abs(i - middle) < Math.abs(poc - middle))) poc = i;
  }
  let vaLow = poc, vaHigh = poc, included = rows[poc].weight;
  while (included < total * 0.7 && (vaLow > 0 || vaHigh + 1 < length)) {
    const below = rows[vaLow - 1]?.weight ?? -1;
    const above = rows[vaHigh + 1]?.weight ?? -1;
    if (above >= below) included += rows[++vaHigh].weight;
    else included += rows[--vaLow].weight;
  }
  return { start: segment[0].time, end: segment.at(-1).time, rows, poc, vaLow, vaHigh };
}

export function sessionProfileData(mode, bars) {
  if (!['tpo', 'session-profile'].includes(mode)) return [];
  const profiles = [];
  let start = 0;
  for (let i = 1; i <= bars.length; i++) {
    // The server encodes exchange-local dates as wall-clock epoch seconds.
    if (i === bars.length || Math.floor(bars[i].time / 86400) !== Math.floor(bars[start].time / 86400)) {
      profiles.push(oneSession(bars.slice(start, i), mode));
      start = i;
    }
  }
  return profiles;
}

export function createSessionProfiles({ chart, series }) {
  let currentMode = '', message = '', profiles = [], requestUpdate = null;

  function paint({ context: ctx, mediaSize: size }) {
    if (!currentMode) return;
    ctx.save();
    const scale = chart.timeScale();
    const spacing = scale.options().barSpacing;
    const range = scale.getVisibleRange();
    for (const profile of profiles) {
      if (!range || profile.end < range.from || profile.start > range.to) continue;
      const first = scale.timeToCoordinate(profile.start), last = scale.timeToCoordinate(profile.end);
      if (first === null || last === null) continue;
      const x = first - spacing / 2;
      const width = Math.max(2, (last - first + spacing) * 0.85);
      const maximum = profile.rows[profile.poc].weight;
      if (maximum <= 0 || x + width < 0 || x > size.width) continue;
      const cellWidth = Math.min(16, width / maximum);
      for (let i = 0; i < profile.rows.length; i++) {
        const row = profile.rows[i];
        if (!row.weight) continue;
        const a = series.priceToCoordinate(row.high), b = series.priceToCoordinate(row.low);
        if (a === null || b === null) continue;
        const y = Math.min(a, b), height = Math.max(2, Math.abs(b - a));
        if (y + height < 0 || y > size.height) continue;
        const inValueArea = i >= profile.vaLow && i <= profile.vaHigh;
        if (currentMode === 'tpo') {
          for (let n = 0; n < row.blocks.length; n++) {
            const block = row.blocks[n], left = x + n * cellWidth;
            if (left + cellWidth < 0 || left > size.width) continue;
            ctx.fillStyle = i === profile.poc ? '#b88238' : 'hsl(' + (198 + block * 5 % 68) + ' 46% ' + (inValueArea ? 44 : 30) + '%)';
            ctx.fillRect(left, y, Math.max(0.5, cellWidth - 0.6), Math.max(1, height - 0.6));
            if (cellWidth >= 7 && height >= 9) {
              ctx.font = Math.min(12, height - 1, cellWidth * 1.28) + 'px monospace';
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';
              ctx.fillStyle = '#f3f4f9';
              ctx.fillText(LETTERS[block % LETTERS.length], left + cellWidth / 2, y + height / 2);
            }
          }
        } else {
          ctx.fillStyle = i === profile.poc ? '#ebae50' : inValueArea ? '#4f91dbd9' : '#475c82bf';
          ctx.fillRect(x, y, Math.max(1, width * row.weight / maximum), Math.max(1, height - 0.8));
        }
      }
    }
    const caption = message || (currentMode === 'tpo'
      ? 'TPO · 거래일별 30분 블록' : '세션 거래량 · 각 봉의 고저 범위에 거래량 균등 배분 (추정)');
    ctx.font = '11px "Segoe UI", "Malgun Gothic", sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#171a21e6';
    ctx.fillRect(7, size.height - 22, ctx.measureText(caption).width + 10, 18);
    ctx.fillStyle = '#d2d6df';
    ctx.fillText(caption, 12, size.height - 13);
    ctx.restore();
  }

  const renderer = { draw: target => target.useMediaCoordinateSpace(paint) };
  const views = [{ zOrder: () => 'normal', renderer: () => renderer }];
  const primitive = {
    attached(info) { requestUpdate = info.requestUpdate; },
    detached() { requestUpdate = null; },
    paneViews() { return views; },
  };
  series.attachPrimitive(primitive);
  function clear() {
    if (!currentMode) return;
    currentMode = '';
    profiles = [];
    message = '';
    requestUpdate?.();
  }
  return {
    setData(mode, bars, tf) {
      if (mode !== 'tpo' && mode !== 'session-profile') { clear(); return; }
      currentMode = mode;
      message = sessionIntervalMessage(mode, tf);
      profiles = message ? [] : sessionProfileData(mode, bars);
      requestUpdate?.();
    },
    clear,
    destroy() { clear(); series.detachPrimitive(primitive); },
  };
}
