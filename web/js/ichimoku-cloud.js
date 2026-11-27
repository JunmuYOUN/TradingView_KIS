// Native line series cover the recorded candles. The cloud uses bar slots so projecting
// past the last candle does not create timestamps shared by drawings, studies or replay.
function addBand(path, left, right) {
  path.moveTo(left.x, left.a);
  path.lineTo(right.x, right.a);
  path.lineTo(right.x, right.b);
  path.lineTo(left.x, left.b);
  path.closePath();
}

function fillSegment(rising, falling, left, right) {
  if (!left.complete || !right.complete) return;
  const leftBand = left.order > 0 ? rising : falling;
  const rightBand = right.order > 0 ? rising : falling;
  if (left.order * right.order < 0) {
    // Interpolate in screen space so the two fills meet on the rendered line crossing,
    // including on a logarithmic or inverted price scale.
    const d0 = left.a - left.b;
    const d1 = right.a - right.b;
    const fraction = d0 === d1 ? 0.5 : d0 / (d0 - d1);
    const y = left.a + fraction * (right.a - left.a);
    const crossing = { x: left.x + fraction * (right.x - left.x), a: y, b: y };
    addBand(leftBand, left, crossing);
    addBand(rightBand, crossing, right);
  } else if (left.order || right.order) {
    addBand(left.order ? leftBand : rightBand, left, right);
  }
}

export function createIchimokuCloud(chart, series, priceSeries = series) {
  const scale = chart.timeScale();
  let a = [], b = [], shift = 0, colorA, colorB, requestUpdate;

  function paint({ context: ctx }) {
    const range = scale.getVisibleLogicalRange();
    if (!range || !a.length) return;
    const first = Math.max(0, Math.floor(range.from) - shift);
    const last = Math.min(a.length - 1, Math.ceil(range.to) - shift);
    if (last < first) return;
    const rising = new Path2D(), falling = new Path2D();
    const tailA = new Path2D(), tailB = new Path2D();
    let previous = null;
    for (let i = first; i <= last; i++) {
      const slot = i + shift;
      const x = scale.logicalToCoordinate(slot);
      if (!Number.isFinite(x)) { previous = null; continue; }
      const ya = Number.isFinite(a[i]) ? priceSeries.priceToCoordinate(a[i]) : null;
      const yb = Number.isFinite(b[i]) ? priceSeries.priceToCoordinate(b[i]) : null;
      const point = { x, a: ya, b: yb, complete: Number.isFinite(ya) && Number.isFinite(yb),
        order: Math.sign(a[i] - b[i]) };
      if (previous) fillSegment(rising, falling, previous, point);
      if (slot >= a.length - 1) {
        // Start the projected lines at the final native line point; preserve warm-up gaps.
        const extendsTail = slot >= a.length;
        if (Number.isFinite(ya)) {
          if (extendsTail && Number.isFinite(previous?.a)) tailA.lineTo(x, ya);
          else tailA.moveTo(x, ya);
        }
        if (Number.isFinite(yb)) {
          if (extendsTail && Number.isFinite(previous?.b)) tailB.lineTo(x, yb);
          else tailB.moveTo(x, yb);
        }
      }
      previous = point;
    }
    ctx.save();
    ctx.fillStyle = colorA;
    ctx.globalAlpha = 0.19;
    ctx.fill(rising);
    ctx.fillStyle = colorB;
    ctx.fill(falling);
    ctx.globalAlpha = 1;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.strokeStyle = colorA;
    ctx.stroke(tailA);
    ctx.strokeStyle = colorB;
    ctx.stroke(tailB);
    ctx.restore();
  }

  const renderer = { draw: target => target.useMediaCoordinateSpace(paint) };
  const views = [{ zOrder: () => 'bottom', renderer: () => renderer }];
  const primitive = {
    attached(info) { requestUpdate = info.requestUpdate; },
    detached() { requestUpdate = null; },
    paneViews() { return views; },
    autoscaleInfo(from, to) {
      // The line series already autoscale all values within the recorded history.
      const first = Math.max(0, a.length - 1 - shift, Math.floor(from) - shift);
      const last = Math.min(a.length - 1, Math.ceil(to) - shift);
      let minValue = Infinity, maxValue = -Infinity;
      for (let i = first; i <= last; i++) {
        for (const value of [a[i], b[i]]) {
          if (!Number.isFinite(value)) continue;
          minValue = Math.min(value, minValue);
          maxValue = Math.max(value, maxValue);
        }
      }
      return minValue === Infinity ? null : { priceRange: { minValue, maxValue } };
    },
  };
  series.attachPrimitive(primitive);
  return {
    setData(spanA, spanB, displacement, spanAColor, spanBColor) {
      a = spanA; b = spanB; shift = displacement; colorA = spanAColor; colorB = spanBColor;
      requestUpdate?.();
    },
    destroy() { series.detachPrimitive(primitive); },
  };
}
