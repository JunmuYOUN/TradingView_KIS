// One plot over the current chart's bars. Parse only the grammar the editor advertises.
const FIELDS = new Set(['open', 'high', 'low', 'close', 'volume', 'hl2', 'hlc3', 'ohlc4']);
const AVERAGES = new Set(['ta.sma', 'ta.ema', 'ta.rsi']);
const PRIORITY = new Map([['+', 1], ['-', 1], ['*', 2], ['/', 2]]);
const finite = value => Number.isFinite(value) ? value : null;

function tokenize(source) {
  if (typeof source !== 'string') throw new Error('plot(...) 형식으로 수식을 입력하세요.');
  if (source.length > 512) throw new Error('수식은 512자까지 입력할 수 있습니다.');
  if (!source.trim()) throw new Error('plot(...) 형식으로 수식을 입력하세요.');
  const pattern = /\s+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)?|[+\-*/(),]/gy;
  const tokens = [];
  let pos = 0;
  while (pos < source.length) {
    const match = pattern.exec(source);
    if (!match) throw new Error(`${pos + 1}번째 문자에는 숫자, 가격 항목 또는 지원하는 연산자를 입력하세요.`);
    if (!/^\s/.test(match[0])) tokens.push(match[0]);
    if (tokens.length > 96) throw new Error('한 수식에 사용할 수 있는 항목은 96개까지입니다.');
    pos = pattern.lastIndex;
  }
  return tokens;
}

function parse(source) {
  const tokens = tokenize(source);
  let index = 0;
  function take(expected) {
    if (tokens[index] !== expected) throw new Error(`'${expected}' 기호가 필요합니다.`);
    index++;
  }
  function expression(depth, minPriority = 0) {
    if (depth > 16) throw new Error('괄호와 함수는 16단계까지 중첩할 수 있습니다.');
    const token = tokens[index++];
    let node;
    if (token === '(') {
      node = expression(depth + 1);
      take(')');
    } else if (token === '-' || token === '+') {
      node = {kind: 'sign', sign: token, arg: expression(depth + 1, 3)};
    } else if (FIELDS.has(token)) {
      node = {kind: 'field', field: token};
    } else if (AVERAGES.has(token)) {
      take('(');
      const arg = expression(depth + 1);
      take(',');
      const rawLength = tokens[index++];
      const length = Number(rawLength);
      if (!/^\d+$/.test(rawLength) || !Number.isInteger(length) || length < 1 || length > 500) {
        throw new Error('함수 기간은 1부터 500까지의 정수로 입력하세요.');
      }
      take(')');
      node = {kind: 'study', op: token, arg, length};
    } else if (token && /^[\d.]/.test(token)) {
      const value = Number(token);
      if (!Number.isFinite(value)) throw new Error('표현 가능한 범위의 숫자를 입력하세요.');
      node = {kind: 'constant', value};
    } else {
      throw new Error('가격·거래량 항목, 숫자 또는 지원하는 함수를 입력하세요.');
    }
    while ((PRIORITY.get(tokens[index]) || 0) > minPriority) {
      const op = tokens[index++];
      node = {kind: 'arithmetic', op, left: node, right: expression(depth + 1, PRIORITY.get(op))};
    }
    return node;
  }
  if (tokens[index++] !== 'plot') throw new Error('수식은 plot(...) 형식이어야 합니다.');
  take('(');
  const result = expression(1);
  take(')');
  if (index !== tokens.length) throw new Error('하나의 plot(...) 수식만 입력할 수 있습니다.');
  return result;
}

function fieldReader(field) {
  const fields = field === 'hl2' ? ['high', 'low'] :
    field === 'hlc3' ? ['high', 'low', 'close'] :
    field === 'ohlc4' ? ['open', 'high', 'low', 'close'] : null;
  if (!fields) return bar => finite(bar?.[field]);
  return bar => {
    let mean = 0;
    for (const key of fields) {
      if (!Number.isFinite(bar?.[key])) return null;
      mean += bar[key] / fields.length;
    }
    return finite(mean);
  };
}

function arithmetic(op, left, right) {
  if (left === null || right === null) return null;
  switch (op) {
    case '+': return finite(left + right);
    case '-': return finite(left - right);
    case '*': return finite(left * right);
    case '/': return right === 0 ? null : finite(left / right);
  }
}

// Streaming state belongs to one evaluation, so a replay or corrected last bar starts cleanly.
function movingAverage(length) {
  const window = new Array(length);
  let slot = 0, valid = 0, average = 0;
  return value => {
    const old = window[slot];
    if (Number.isFinite(old)) { average -= old / length; valid--; }
    window[slot] = value;
    slot = (slot + 1) % length;
    if (value !== null) { average += value / length; valid++; }
    return valid === length ? finite(average) : null;
  };
}

function exponentialAverage(length) {
  const weight = 2 / (length + 1);
  let previous = null;
  return value => {
    if (value === null) return null;
    previous = previous === null ? value : finite(previous * (1 - weight) + value * weight);
    return previous;
  };
}

function relativeStrength(length) {
  let previous = null, changes = 0, gain = 0, loss = 0;
  return value => {
    if (value === null) {
      previous = null;
      changes = gain = loss = 0;
      return null;
    }
    // Scaling both gains and losses equally keeps a jump between extreme prices finite.
    const change = previous === null ? null : value / 2 - previous / 2;
    previous = value;
    if (change === null) return null;
    const up = Math.max(0, change), down = Math.max(0, -change);
    if (changes < length) {
      gain += up / length;
      loss += down / length;
      if (++changes < length) return null;
    } else {
      gain = gain * (1 - 1 / length) + up / length;
      loss = loss * (1 - 1 / length) + down / length;
    }
    if (gain === 0 && loss === 0) return 50;
    if (loss === 0) return 100;
    return finite(100 / (1 + loss / gain));
  };
}

function createReader(node) {
  switch (node.kind) {
    case 'field': return fieldReader(node.field);
    case 'constant': return () => node.value;
    case 'sign': {
      const read = createReader(node.arg);
      return bar => {
        const value = read(bar);
        return value === null ? null : node.sign === '-' ? -value : value;
      };
    }
    case 'arithmetic': {
      const left = createReader(node.left), right = createReader(node.right);
      return bar => arithmetic(node.op, left(bar), right(bar));
    }
    case 'study': {
      const read = createReader(node.arg);
      const advance = node.op === 'ta.sma' ? movingAverage(node.length) :
        node.op === 'ta.ema' ? exponentialAverage(node.length) : relativeStrength(node.length);
      return bar => advance(read(bar));
    }
  }
}

export function compilePersonalScript(source) {
  const tree = parse(source);
  return {
    evaluate(bars) {
      if (!Array.isArray(bars)) throw new Error('차트의 봉 데이터가 필요합니다.');
      return Array.from(bars, createReader(tree));
    },
  };
}
