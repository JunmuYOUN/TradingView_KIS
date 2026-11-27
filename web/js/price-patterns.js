// Close-based price patterns on the original bar timeline. A long gap is stored as
// one integer run, so an extreme move never allocates one object per synthetic box.
const UP = '#ef5350';
const DOWN = '#4979ff';
const YANG = '#40b99a';
const YIN = '#dd6d79';

export function isPricePattern(mode) {
  return mode === 'renko' || mode === 'pnf' || mode === 'kagi';
}

// Fix the traditional box/reversal amount once, from the first supplied close.
// Appending or replacing a later bar cannot change a historical threshold.
export function patternBoxSize(bars, minMove = 0) {
  const seed = Number(bars[0]?.close);
  const tick = Number.isFinite(minMove) && minMove > 0 ? minMove : 0;
  const raw = Number.isFinite(seed) && seed !== 0 ? Math.abs(seed) / 100 : tick || 1;
  return Number((tick ? Math.max(1, Math.round(raw / tick)) * tick : raw).toPrecision(12));
}

function carry(time, close) {
  return { time, close, high: close, low: close };
}

function roundedIndex(price, box, direction) {
  const value = price / box;
  // Correct floating-point noise at an exact box boundary without rounding a
  // genuine partial price move to the next box.
  const epsilon = Math.min(1e-8, Number.EPSILON * Math.max(1, Math.abs(value)) * 4);
  return direction > 0 ? Math.floor(value + epsilon) : Math.ceil(value - epsilon);
}

function gridData(bars, box, pnf) {
  let end = roundedIndex(bars[0].close, box, 1);
  let direction = 0;
  let start = end;
  let lastColumn = -1;
  const out = [carry(bars[0].time, end * box)];
  for (let i = 1; i < bars.length; i++) {
    const { time, close } = bars[i];
    const rising = close > end * box;
    const side = rising ? 1 : -1;
    const target = roundedIndex(close, box, side);
    const reversing = direction !== 0 && side !== direction;
    const threshold = reversing ? (pnf ? 3 : 2) : 1;
    if (!Number.isSafeInteger(target) || (target - end) * side < threshold) {
      out.push(carry(time, end * box));
      continue;
    }
    if (pnf) {
      if (direction !== side) start = end + side;
      else {
        // A point-and-figure column extends as one column. Timestamp the
        // completed column at its latest contributing bar.
        const old = out[lastColumn];
        out[lastColumn] = carry(old.time, old.close);
      }
    } else {
      // A reversal starts at the previous brick's open (a one-box offset).
      start = reversing ? end + side : end;
    }
    end = target;
    direction = side;
    const open = start * box;
    const finish = end * box;
    const padding = pnf ? box / 2 : 0;
    out.push({ time, box, open, close: finish, direction, count: Math.abs(end - start) + Number(pnf),
      high: Math.max(open, finish) + padding, low: Math.min(open, finish) - padding });
    if (pnf) lastColumn = i;
  }
  return out;
}

function kagiData(bars, reversal) {
  const seed = bars[0].close;
  let end = seed, direction = 0, shoulder = null, waist = null;
  let current = null;
  const legs = [];
  const out = [carry(bars[0].time, seed)];
  for (let i = 1; i < bars.length; i++) {
    const { close, time } = bars[i];
    const difference = close - end;
    const side = Math.sign(difference);
    const tolerance = Number.EPSILON * Math.max(1, Math.abs(end), Math.abs(close)) * 4;
    const reversalMove = side !== direction && Math.abs(difference) + tolerance >= reversal;
    if (Number.isFinite(close) && side && (!direction || side === direction || reversalMove)) {
      if (side !== direction) {
        if (direction > 0) shoulder = end;
        if (direction < 0) waist = end;
        if (!direction && side > 0) waist = seed;
        if (!direction && side < 0) shoulder = seed;
        current = { open: end, startYang: current ? current.endYang : side > 0,
          threshold: side > 0 ? shoulder : waist, direction: side };
        legs.push(current);
      }
      // A return across a previous shoulder changes yin to yang. A break
      // below the preceding waist changes yang to yin; split at that level.
      const broken = current.threshold !== null &&
        (side > 0 ? !current.startYang && close > current.threshold : current.startYang && close < current.threshold);
      current.split = broken ? current.threshold : null;
      current.endYang = broken ? side > 0 : current.startYang;
      current.index = i;
      current.close = close;
      end = close;
      direction = side;
    }
    out.push(carry(time, end));
  }
  let previousIndex = 0;
  for (const leg of legs) {
    // Divide the horizontal connector across original timestamps so it remains
    // visible when the viewport lies between two widely spaced reversal legs.
    for (let i = previousIndex + 1; i <= leg.index; i++) {
      const row = out[i];
      row.bridge = leg.open;
      row.bridgeYang = leg.startYang;
      row.high = Math.max(row.high, leg.open);
      row.low = Math.min(row.low, leg.open);
    }
    const row = out[leg.index];
    row.leg = leg;
    row.high = Math.max(leg.open, leg.close);
    row.low = Math.min(leg.open, leg.close);
    previousIndex = leg.index;
  }
  return out;
}

export function pricePatternData(mode, bars, box) {
  if (!isPricePattern(mode) || !bars.length || !Number.isFinite(bars[0].close) ||
      !Number.isFinite(box) || box <= 0) return [];
  return mode === 'kagi' ? kagiData(bars, box) : gridData(bars, box, mode === 'pnf');
}

function stroke(ctx, x1, y1, x2, y2, color, width) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

function drawKagi(ctx, row, x, spacing, y) {
  function segment(x1, p1, x2, p2, yang) {
    stroke(ctx, x1, y(p1), x2, y(p2), yang ? YANG : YIN, yang ? 2.8 : 1.2);
  }
  if (row.bridge !== undefined) segment(x - spacing, row.bridge, x, row.bridge, row.bridgeYang);
  const leg = row.leg;
  if (!leg) return;
  if (leg.split !== null) segment(x, leg.open, x, leg.split, leg.startYang);
  segment(x, leg.split ?? leg.open, x, leg.close, leg.endYang);
}

function drawRun(ctx, row, x, spacing, y, pnf) {
  if (!row.count) return;
  const color = row.direction > 0 ? UP : DOWN;
  const w = spacing * .85;
  const left = x - w / 2;
  const cellWidth = w / row.count;
  const sy = y(row.open), ey = y(row.close);
  const pixelHeight = Math.abs(y(row.open + row.box) - sy);
  // At sub-pixel resolution individual glyphs cannot be distinguished. Keep
  // the run's full extent and direction while bounding work per visible bar.
  if (row.count > 512 || pixelHeight < 1.6 || (!pnf && cellWidth < .65)) {
    if (pnf) stroke(ctx, x, y(row.high), x, y(row.low), color, 1.5);
    else stroke(ctx, left, sy, left + w, ey, color, 1.5);
    return;
  }
  for (let i = 0; i < row.count; i++) {
    const p = row.open + i * row.direction * row.box;
    if (pnf) {
      const top = y(p + row.box * .4), bottom = y(p - row.box * .4);
      const half = Math.max(.45, Math.min(w * .47, Math.abs(top - bottom) / 2));
      if (row.direction > 0) {
        stroke(ctx, x - half, top, x + half, bottom, color, 1.25);
        stroke(ctx, x + half, top, x - half, bottom, color, 1.25);
      } else {
        ctx.beginPath();
        ctx.ellipse(x, (top + bottom) / 2, half, Math.max(.45, Math.abs(top - bottom) / 2), 0, 0, 2 * Math.PI);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.25;
        ctx.stroke();
      }
    } else {
      const a = y(p), b = y(p + row.direction * row.box);
      ctx.fillStyle = color;
      ctx.fillRect(left + i * cellWidth, Math.min(a, b), Math.max(.65, cellWidth - .5), Math.max(1, Math.abs(b - a)));
    }
  }
}

export function pricePatternView(mode) {
  let frame;
  const renderer = {
    draw(target, y) {
      if (!frame?.visibleRange) return;
      const from = Math.max(0, frame.visibleRange.from - 1);
      const to = Math.min(frame.bars.length, frame.visibleRange.to + 1);
      const spacing = frame.barSpacing * (frame.conflationFactor || 1);
      target.useMediaCoordinateSpace(({ context: ctx }) => {
        for (let i = from; i < to; i++) {
          const { x, originalData } = frame.bars[i];
          if (mode === 'kagi') drawKagi(ctx, originalData, x, spacing, y);
          else drawRun(ctx, originalData, x, spacing, y, mode === 'pnf');
        }
      });
    },
  };
  return {
    update(data) { frame = data; },
    renderer: () => renderer,
    priceValueBuilder: row => [row.high, row.low, row.close],
    isWhitespace: row => row.close == null,
    defaultOptions: () => ({ ...window.LightweightCharts.customSeriesDefaultOptions, color: UP }),
  };
}
