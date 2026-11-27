// All study outputs use the original OHLCV timestamps. null denotes an unavailable value.
function sma(values, length) {
  let sum = 0, count = 0;
  return values.map((value, i) => {
    if (Number.isFinite(value)) { sum += value; count++; }
    const leaving = values[i - length];
    if (Number.isFinite(leaving)) { sum -= leaving; count--; }
    return count === length ? sum / length : null;
  });
}

function ema(values, length) {
  const alpha = 2 / (length + 1);
  let previous = null;
  return values.map(value => {
    if (!Number.isFinite(value)) return null;
    previous = previous === null ? value : previous + alpha * (value - previous);
    return previous;
  });
}

function wilder(values, length) {
  let initialSum = 0, count = 0, previous = null;
  return values.map(value => {
    if (!Number.isFinite(value)) return null;
    if (count < length) {
      initialSum += value;
      if (++count === length) previous = initialSum / length;
    } else previous += (value - previous) / length;
    return previous;
  });
}

function rollingExtreme(bars, length, field, wins) {
  const result = Array(bars.length).fill(null);
  const candidates = [];
  let head = 0;
  let lastInvalid = -1;
  for (let i = 0; i < bars.length; i++) {
    if (candidates[head] <= i - length) head++;
    const value = Number(bars[i][field]);
    if (Number.isNaN(value)) {
      lastInvalid = i;
    } else {
      // A newer, more extreme value outlives and replaces older candidates.
      while (candidates.length > head && wins(value, bars[candidates.at(-1)][field])) candidates.pop();
      candidates.push(i);
    }
    if (i + 1 >= length) {
      result[i] = lastInvalid > i - length ? NaN : Number(bars[candidates[head]][field]);
    }
  }
  return result;
}

function rollingRange(bars, length) {
  return {
    high: rollingExtreme(bars, length, 'high', (a, b) => a >= b),
    low: rollingExtreme(bars, length, 'low', (a, b) => a <= b),
  };
}

function rangeMidpoint(bars, length) {
  const { high, low } = rollingRange(bars, length);
  return high.map((value, i) => Number.isFinite(value) && Number.isFinite(low[i]) ? (value + low[i]) / 2 : null);
}

const typical = bar => (bar.high + bar.low + bar.close) / 3;

const length = (value, name = 'length', label = '기간') => ({ name, label, value, min: 1, max: 500, step: 1 });
export const STUDY_PARAMETERS = {
  rsi: [length(14)], sma: [length(20)], ema: [length(9)], wma: [length(9)],
  bb: [length(20), { name: 'multiplier', label: '표준편차 배수', value: 2, min: 0.1, max: 10, step: 0.1 }],
  macd: [length(12, 'fast', '단기 기간'), length(26, 'slow', '장기 기간'), length(9, 'signal', '신호선 기간')],
  stoch: [length(14), length(3, 'k', '%K 평활'), length(3, 'd', '%D 평활')],
  atr: [length(14)], donchian: [length(20)], roc: [length(9)], momentum: [length(10)],
  ichimoku: [length(9, 'conversion', '전환선 기간'), length(26, 'base', '기준선 기간'),
    length(52, 'spanB', '선행스팬 B 기간'), length(26, 'displacement', '이동 봉 수')],
};

export function studyParameters(id, input = {}) {
  const params = {};
  for (const field of STUDY_PARAMETERS[id] || []) {
    const n = input?.[field.name];
    params[field.name] = Number.isFinite(n) && n >= field.min && n <= field.max &&
      (field.step !== 1 || Number.isInteger(n)) ? n : field.value;
  }
  if (id === 'macd' && params.fast >= params.slow) { params.fast = 12; params.slow = 26; }
  return params;
}

export function vwapAnchor(tf) {
  if (tf === 'D') return '월';
  if (tf === 'W' || tf === 'M') return '연';
  return '세션';
}

// History-dependent studies (e.g. Wilder smoothing) need their full seed. A finite-window
// study can compute its latest point using only the bars on which that point depends.
export function studyTailBars(id, bars, params) {
  let length;
  switch (id) {
    case 'sma': case 'bb': case 'wma': case 'donchian': length = params.length; break;
    case 'roc': case 'momentum': length = params.length + 1; break;
    case 'stoch': length = params.length + params.k + params.d - 2; break;
    case 'ichimoku': length = Math.max(params.conversion, params.base, params.spanB); break;
    default: return bars;
  }
  return bars.length > length ? bars.slice(-length) : bars;
}

export function studyValues(id, bars, tf, options, isIndex = false) {
  const params = studyParameters(id, options);
  const n = params.length;
  const close = bars.map(bar => bar.close);
  switch (id) {
    case 'volume24h': {
      if (tf === 'W' || tf === 'M') return [[]];
      const volumeValue = isIndex ? bar => bar.volume : bar => bar.close * bar.volume;
      let start = 0, sum = 0;
      return [bars.map((bar, i) => {
        sum += volumeValue(bar);
        const cutoff = bar.time - 86400;
        while (start < i && bars[start].time <= cutoff) {
          sum -= volumeValue(bars[start]);
          start++;
        }
        return Math.max(0, sum);
      })];
    }
    case 'vwap': {
      let previousAnchor = null, amount = 0, volume = 0;
      return [bars.map(bar => {
        // KIS timestamps in this app encode the exchange's wall clock as UTC seconds.
        let anchor = Math.floor(bar.time / 86400);
        if (tf === 'D' || tf === 'W' || tf === 'M') {
          const date = new Date(bar.time * 1000);
          anchor = tf === 'D' ? date.getUTCFullYear() * 12 + date.getUTCMonth() : date.getUTCFullYear();
        }
        if (anchor !== previousAnchor) { previousAnchor = anchor; amount = 0; volume = 0; }
        amount += typical(bar) * bar.volume;
        volume += bar.volume;
        return volume > 0 ? amount / volume : null;
      })];
    }
    case 'rsi': {
      const changes = close.map((value, i) => i ? value - close[i - 1] : null);
      const gains = wilder(changes.map(v => v === null ? null : Math.max(0, v)), n);
      const losses = wilder(changes.map(v => v === null ? null : Math.max(0, -v)), n);
      return [gains.map((gain, i) => {
        if (gain === null) return null;
        if (losses[i] === 0 && gain === 0) return 50;
        return 100 * gain / (gain + losses[i]);
      })];
    }
    case 'sma': return [sma(close, n)];
    case 'ema': return [ema(close, n)];
    case 'wma': {
      return [close.map((_, i) => {
        if (i + 1 < n) return null;
        let sum = 0;
        for (let j = 0; j < n; j++) sum += (n - j) * close[i - j];
        return sum / (n * (n + 1) / 2);
      })];
    }
    case 'bb': {
      const mid = sma(close, n);
      const spread = mid.map((mean, i) => {
        if (mean === null) return null;
        let sum = 0;
        for (let j = i - n + 1; j <= i; j++) sum += (close[j] - mean) ** 2;
        return params.multiplier * Math.sqrt(sum / n);
      });
      return [mid, mid.map((v, i) => v === null ? null : v + spread[i]),
        mid.map((v, i) => v === null ? null : v - spread[i])];
    }
    case 'macd': {
      const fast = ema(close, params.fast), slow = ema(close, params.slow);
      const macd = fast.map((v, i) => v - slow[i]);
      const signal = ema(macd, params.signal);
      return [macd.map((v, i) => v - signal[i]), macd, signal];
    }
    case 'atr': {
      const tr = bars.map((b, i) => i ? Math.max(b.high - b.low, Math.abs(b.high - close[i - 1]),
        Math.abs(b.low - close[i - 1])) : b.high - b.low);
      return [wilder(tr, n)];
    }
    case 'stoch': {
      const { high, low } = rollingRange(bars, n);
      const raw = close.map((value, i) => high[i] === null || high[i] === low[i]
        ? null : 100 * (value - low[i]) / (high[i] - low[i]));
      const k = sma(raw, params.k);
      return [k, sma(k, params.d)];
    }
    case 'donchian': {
      const { high, low } = rollingRange(bars, n);
      return [high, high.map((v, i) => v === null ? null : (v + low[i]) / 2), low];
    }
    case 'ichimoku': {
      const conversion = rangeMidpoint(bars, params.conversion);
      const base = rangeMidpoint(bars, params.base);
      const spanA = conversion.map((value, i) => value === null || base[i] === null ? null : (value + base[i]) / 2);
      const spanB = rangeMidpoint(bars, params.spanB);
      // Each value belongs to its source bar. The chart displaces the spans by bar count.
      return [conversion, base, spanA, spanB, close.map(value => Number.isFinite(value) ? value : null)];
    }
    case 'obv': {
      let cumulative = 0;
      return [bars.map((b, i) => {
        if (i) cumulative += Math.sign(b.close - close[i - 1]) * b.volume;
        return cumulative;
      })];
    }
    case 'roc':
      return [close.map((v, i) => i < n || !close[i - n] ? null : 100 * (v / close[i - n] - 1))];
    case 'momentum':
      return [close.map((v, i) => i < n ? null : v - close[i - n])];
    default: return [];
  }
}
