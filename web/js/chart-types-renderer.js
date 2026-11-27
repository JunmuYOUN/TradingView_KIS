const LINE = '#5792ff';
const UP = '#ef5350';
const DOWN = '#4979ff';

function hlcBand(context, points) {
  function trace(key, reverse = false, connect = false) {
    for (let j = 0; j < points.length; j++) {
      const p = points[reverse ? points.length - 1 - j : j];
      if (j || connect) context.lineTo(p.x, p[key]);
      else context.moveTo(p.x, p[key]);
    }
  }
  for (const [key, fill] of [['high', 'rgba(94,139,242,.26)'], ['low', 'rgba(245,154,73,.23)']]) {
    context.beginPath();
    trace(key);
    trace('close', true, true);
    context.closePath();
    context.fillStyle = fill;
    context.fill();
  }
  for (const [key, color] of [['high', '#6399ec'], ['low', '#d69562'], ['close', '#b9c6dd']]) {
    context.beginPath();
    trace(key);
    context.strokeStyle = color;
    context.lineWidth = key === 'close' ? 1.6 : 1;
    context.stroke();
  }
}

export function customChartView(mode) {
  const LC = window.LightweightCharts;
  let frame;
  const renderer = {
    draw(target, priceY) {
      if (!frame?.visibleRange) return;
      const from = Math.max(0, frame.visibleRange.from - 1);
      const to = Math.min(frame.bars.length, frame.visibleRange.to + 1);
      if (to <= from) return;
      const items = frame.bars.slice(from, to);
      const spacing = frame.barSpacing * (frame.conflationFactor || 1);
      target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
        if (mode === 'hlc-area') {
          hlcBand(ctx, items.map(({ x, originalData: b }) =>
            ({ x, high: priceY(b.high), low: priceY(b.low), close: priceY(b.close) })));
          return;
        }
        let maxVolume = 1;
        if (mode === 'volume-candles') {
          for (const { originalData: b } of items) maxVolume = Math.max(maxVolume, b.volume || 0);
        }
        for (const { x, originalData: b } of items) {
          const width = Math.max(1, spacing * .72);
          const high = priceY(b.high), low = priceY(b.low);
          const close = priceY(b.close);
          if (mode === 'columns') {
            ctx.fillStyle = b.color;
            ctx.fillRect(x - width / 2, close, width, Math.max(1, mediaSize.height - close));
          } else if (mode === 'high-low') {
            ctx.fillStyle = LINE;
            ctx.fillRect(x - width / 2, high, width, Math.max(1, low - high));
            if (spacing > 54) {
              ctx.fillStyle = '#ced6e3';
              ctx.font = '10px "Segoe UI", sans-serif';
              ctx.textAlign = 'center';
              ctx.fillText(b.high.toLocaleString('ko-KR', { maximumFractionDigits: 4 }), x, high - 4);
              ctx.fillText(b.low.toLocaleString('ko-KR', { maximumFractionDigits: 4 }), x, low + 12);
            }
          } else {
            const open = priceY(b.open);
            const candleWidth = Math.max(1, spacing * (.12 + .78 * Math.max(0, b.volume || 0) / maxVolume));
            ctx.fillStyle = b.close >= b.open ? UP : DOWN;
            ctx.fillRect(x - .5, high, 1, Math.max(1, low - high));
            ctx.fillRect(x - candleWidth / 2, Math.min(open, close), candleWidth, Math.max(1, Math.abs(close - open)));
          }
        }
      });
    },
  };
  return {
    renderer: () => renderer,
    update(data) { frame = data; },
    priceValueBuilder: b => mode === 'columns' ? [b.close] : [b.high, b.low, b.close],
    isWhitespace: b => b.close == null,
    defaultOptions: () => ({ ...LC.customSeriesDefaultOptions, color: LINE }),
  };
}

// Keep the original event timestamp, so indicators and saved drawing coordinates retain the same time mapping.
export function nextBreak(lines, bar, seed) {
  const recent = lines.slice(-3);
  const previous = recent.at(-1);
  const upper = previous ? Math.max(...recent.map(p => p.high)) : seed;
  const lower = previous ? Math.min(...recent.map(p => p.low)) : seed;
  if (bar.close <= upper && bar.close >= lower) return null;
  const open = previous ? (bar.close > upper ? previous.high : previous.low) : seed;
  const close = bar.close;
  return { time: bar.time, open, close, high: Math.max(open, close), low: Math.min(open, close) };
}

export function lineBreakData(bars) {
  const lines = [];
  for (let i = 1; i < bars.length; i++) {
    const line = nextBreak(lines, bars[i], bars[0].close);
    if (line) lines.push(line);
  }
  return lines;
}

export function pricePoint(mode, bar, previousBar, previousPoint) {
  const { time, open, high, low, close } = bar;
  const color = close >= (previousBar?.close ?? open) ? UP : DOWN;
  if (mode === 'heikin-ashi') {
    const haClose = (open + high + low + close) / 4;
    const haOpen = previousPoint ? (previousPoint.open + previousPoint.close) / 2 : (open + close) / 2;
    return { time, open: haOpen, high: Math.max(high, haOpen, haClose), low: Math.min(low, haOpen, haClose), close: haClose };
  }
  if (mode === 'hollow') return { time, open, high, low, close, color: close >= open ? 'transparent' : color, wickColor: color, borderColor: color };
  if (mode === 'bars') return { time, open, high, low, close };
  if (['high-low', 'volume-candles', 'hlc-area', 'columns'].includes(mode)) return { ...bar, color };
  return { time, value: close };
}

export function makePriceSeries(chart, mode, priceFormat) {
  const LC = window.LightweightCharts;
  const options = { priceFormat, priceScaleId: 'right' };
  if (['hlc-area', 'high-low', 'columns', 'volume-candles'].includes(mode)) {
    return chart.addCustomSeries(customChartView(mode), { ...options, color: LINE });
  }
  if (mode === 'bars') return chart.addSeries(LC.BarSeries, { ...options, upColor: UP, downColor: DOWN, thinBars: true });
  if (mode === 'hollow' || mode === 'heikin-ashi' || mode === 'line-break') {
    return chart.addSeries(LC.CandlestickSeries, { ...options, upColor: UP, downColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN, borderVisible: mode === 'hollow', wickVisible: mode !== 'line-break' });
  }
  if (mode === 'area') return chart.addSeries(LC.AreaSeries, { ...options, lineColor: LINE, lineWidth: 2, topColor: 'rgba(65,125,242,.42)', bottomColor: 'rgba(65,125,242,.015)' });
  if (mode === 'baseline') return chart.addSeries(LC.BaselineSeries, { ...options, topLineColor: '#29a78b', bottomLineColor: '#e26474', lineWidth: 2,
    topFillColor1: 'rgba(41,167,139,.35)', topFillColor2: 'rgba(41,167,139,.04)', bottomFillColor1: 'rgba(226,100,116,.04)', bottomFillColor2: 'rgba(226,100,116,.35)' });
  return chart.addSeries(LC.LineSeries, { ...options, color: LINE, lineWidth: 2,
    pointMarkersVisible: mode === 'markers', pointMarkersRadius: 3, lineType: mode === 'step' ? LC.LineType.WithSteps : LC.LineType.Simple });
}
