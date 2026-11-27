// 차트 선긋기 도구: 추세선, 연장선, 수평선, 피보나치 되돌림, 평행 채널, 엘리엇 파동.
//
// 구조
//  - DrawingPrimitive: Lightweight Charts 의 series primitive. 차트가 다시 그려질 때마다
//    DrawingManager.render() 를 호출해 같은 캔버스에 선을 그립니다 (확대·스크롤과 자동 동기화).
//  - DrawingManager: 도구 선택, 클릭으로 점 찍기, 선택·드래그·삭제, 되돌리기, 저장을 담당합니다.
//  - 각 점은 { time, price } 로 저장하므로 타임프레임을 바꿔도 같은 위치에 남습니다.

export const TOOLS = {
  cursor: { label: '선택', key: 'v', points: 0 },
  trend: { label: '추세선', key: 't', points: 2, color: '#2962ff' },
  ray: { label: '연장선', key: 'r', points: 2, color: '#2962ff' },
  hline: { label: '수평선', key: 'h', points: 1, color: '#f7a600' },
  fib: { label: '피보나치', key: 'f', points: 2, color: '#787b86' },
  channel: { label: '평행 채널', key: 'c', points: 3, color: '#2962ff' },
  elliott5: { label: '충격파 12345', key: 'e', points: 6, color: '#3179f5' },
  elliottABC: { label: '조정파 ABC', key: 'a', points: 4, color: '#e91e63' },
};

export const FIB_LEVELS = [
  { r: 0, c: '#787b86' },
  { r: 0.236, c: '#f23645' },
  { r: 0.382, c: '#ff9800' },
  { r: 0.5, c: '#4caf50' },
  { r: 0.618, c: '#089981' },
  { r: 0.786, c: '#00bcd4' },
  { r: 1, c: '#787b86' },
  { r: 1.618, c: '#2962ff' },
  { r: 2.618, c: '#9c27b0' },
];

const HIT_PX = 6;
const ANCHOR_R = 5;

// 가격 표시 소수 자릿수: 국내 0 (원), 해외 2 (달러). 종목을 바꿀 때 app.js 가 맞춥니다.
let priceDecimals = 0;
export function setPriceDecimals(n) {
  priceDecimals = n;
}
const fmtPrice = (p) => p.toLocaleString('ko-KR', { minimumFractionDigits: priceDecimals, maximumFractionDigits: priceDecimals });
const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

function hexToRgba(hex, a) {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  let t = len2 ? ((px - x1) * dx + (py - y1) * dy) / len2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

// 선분 (x1,y1)->(x2,y2) 를 x2 방향으로 화면 끝까지 늘립니다.
function extendToEdge(x1, y1, x2, y2, w, h) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  if (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9) return [x2, y2];
  const big = 4 * (w + h) / Math.hypot(dx, dy);
  return [x1 + dx * big, y1 + dy * big];
}

// 엘리엇 충격파 기본 규칙 검사 (점 0~5)
export function checkImpulse(pts) {
  if (pts.length < 6) return [];
  const p = pts.map((x) => x.price);
  const dir = Math.sign(p[1] - p[0]) || 1;
  const msgs = [];
  if (dir * (p[2] - p[0]) <= 0) msgs.push('2파가 1파 시작점을 넘었습니다');
  const w1 = Math.abs(p[1] - p[0]);
  const w3 = Math.abs(p[3] - p[2]);
  const w5 = Math.abs(p[5] - p[4]);
  if (w3 < w1 && w3 < w5) msgs.push('3파가 가장 짧습니다');
  if (dir * (p[4] - p[1]) <= 0) msgs.push('4파가 1파 영역과 겹칩니다');
  return msgs;
}

class DrawingPrimitive {
  constructor(manager) {
    this.manager = manager;
    const renderer = {
      draw: (target) => {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          this.manager.render(context, mediaSize);
        });
      },
    };
    this._views = [{ zOrder: () => 'top', renderer: () => renderer }];
  }

  attached({ requestUpdate }) {
    this.manager._requestUpdate = requestUpdate;
  }

  detached() {
    this.manager._requestUpdate = null;
  }

  updateAllViews() {}

  paneViews() {
    return this._views;
  }
}

export class DrawingManager {
  /**
   * @param {object} o
   * @param {import('lightweight-charts').IChartApi} o.chart
   * @param {import('lightweight-charts').ISeriesApi} o.series 캔들 시리즈
   * @param {HTMLElement} o.container 차트 컨테이너
   * @param {import('./timemap.js').TimeMap} o.timeMap
   * @param {() => Array} o.getBars 현재 봉 배열
   */
  constructor({ chart, series, container, timeMap, getBars }) {
    this.chart = chart;
    this.series = series;
    this.container = container;
    this.timeMap = timeMap;
    this.getBars = getBars;

    this.drawings = [];
    this.tool = 'cursor';
    this.magnet = true;
    this.draft = null;      // { type, points: [...], hover }
    this.selectedId = null;
    this.hoverId = null;
    this.drag = null;
    this.size = { width: 0, height: 0 };
    this.history = [];

    this.onChange = () => {};     // 선이 바뀌었을 때 (저장용)
    this.onSelect = () => {};     // 선택이 바뀌었을 때 (스타일 패널용)
    this.onToolChange = () => {};

    this._requestUpdate = null;
    this.primitive = new DrawingPrimitive(this);
    series.attachPrimitive(this.primitive);
    this._bindEvents();
  }

  // ------------------------------------------------------------ 상태
  update() {
    if (this._requestUpdate) this._requestUpdate();
  }

  load(drawings) {
    this.drawings = Array.isArray(drawings) ? drawings : [];
    this.selectedId = null;
    this.draft = null;
    this.history = [];
    this.onSelect(null);
    this.update();
  }

  toJSON() {
    return this.drawings;
  }

  _snapshot() {
    this.history.push(JSON.stringify(this.drawings));
    if (this.history.length > 100) this.history.shift();
  }

  undo() {
    const s = this.history.pop();
    if (s === undefined) return;
    this.drawings = JSON.parse(s);
    if (!this.drawings.find((d) => d.id === this.selectedId)) {
      this.selectedId = null;
      this.onSelect(null);
    }
    this.update();
    this.onChange();
  }

  setTool(tool) {
    if (!TOOLS[tool]) return;
    this.tool = tool;
    this.draft = null;
    this.container.style.cursor = tool === 'cursor' ? '' : 'crosshair';
    this.onToolChange(tool);
    this.update();
  }

  setMagnet(on) {
    this.magnet = !!on;
  }

  get selected() {
    return this.drawings.find((d) => d.id === this.selectedId) || null;
  }

  select(id) {
    this.selectedId = id;
    this.onSelect(this.selected);
    this.update();
  }

  deleteSelected() {
    if (!this.selectedId) return;
    this._snapshot();
    this.drawings = this.drawings.filter((d) => d.id !== this.selectedId);
    this.select(null);
    this.onChange();
  }

  clearAll() {
    if (!this.drawings.length) return;
    this._snapshot();
    this.drawings = [];
    this.select(null);
    this.onChange();
  }

  setSelectedStyle(patch) {
    const d = this.selected;
    if (!d) return;
    this._snapshot();
    d.style = { ...d.style, ...patch };
    this.update();
    this.onChange();
  }

  // ------------------------------------------------------------ 좌표 변환
  _paneSize() {
    const ts = this.chart.timeScale();
    return {
      width: ts.width(),
      height: this.container.clientHeight - ts.height(),
    };
  }

  toPx(pt) {
    const x = this.chart.timeScale().logicalToCoordinate(this.timeMap.toLogical(pt.time));
    const y = this.series.priceToCoordinate(pt.price);
    if (x === null || y === null) return null;
    return { x, y };
  }

  /** 화면 좌표 -> { time, price }. 봉 중앙에 맞추고, 자석 모드면 OHLC 에 붙입니다. */
  fromPx(x, y, useMagnet = this.magnet) {
    const lg = this.chart.timeScale().coordinateToLogical(x);
    let price = this.series.coordinateToPrice(y);
    if (lg === null || price === null) return null;
    const li = Math.round(lg);
    if (useMagnet) {
      const bar = this.getBars()[li];
      if (bar) {
        let best = price;
        let bestD = Infinity;
        for (const v of [bar.open, bar.high, bar.low, bar.close]) {
          const vy = this.series.priceToCoordinate(v);
          if (vy === null) continue;
          const d = Math.abs(vy - y);
          if (d < bestD) { bestD = d; best = v; }
        }
        if (bestD < 40) price = best;
      }
    }
    return { time: this.timeMap.toTime(li), price };
  }

  _localXY(ev) {
    const r = this.container.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  // ------------------------------------------------------------ 기하 계산
  /**
   * 선 하나를 화면 요소(선분, 채우기, 글자, 앵커)로 바꿉니다. 렌더링과 클릭 판정이 같이 씁니다.
   */
  geometry(d, points = d.points) {
    const { width: W, height: H } = this.size;
    const px = points.map((p) => this.toPx(p));
    if (px.some((p) => p === null)) return null;
    const color = (d.style && d.style.color) || TOOLS[d.type].color;
    const lw = (d.style && d.style.width) || 1.5;
    const g = { lines: [], fills: [], labels: [], anchors: px, color, lw };
    const line = (a, b, extra = {}) => g.lines.push({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, color, width: lw, ...extra });

    switch (d.type) {
      case 'trend':
        if (px.length >= 2) line(px[0], px[1]);
        break;

      case 'ray':
        if (px.length >= 2) {
          const [ex, ey] = extendToEdge(px[0].x, px[0].y, px[1].x, px[1].y, W, H);
          line(px[0], { x: ex, y: ey });
        }
        break;

      case 'hline': {
        const y = px[0].y;
        g.lines.push({ x1: 0, y1: y, x2: W, y2: y, color, width: lw });
        g.labels.push({ x: W - 6, y: y - 4, text: fmtPrice(points[0].price), color, align: 'right' });
        break;
      }

      case 'fib': {
        if (px.length < 2) break;
        const [a, b] = points;
        const xL = Math.min(px[0].x, px[1].x);
        const xR = d.style && d.style.extend ? W : Math.max(px[0].x, px[1].x);
        const ys = [];
        for (const lv of FIB_LEVELS) {
          const price = b.price + (a.price - b.price) * lv.r;
          const y = this.series.priceToCoordinate(price);
          if (y === null) continue;
          ys.push({ y, lv, price });
          g.lines.push({ x1: xL, y1: y, x2: xR, y2: y, color: lv.c, width: 1 });
          g.labels.push({ x: xL + 4, y: y - 3, text: `${lv.r} (${fmtPrice(price)})`, color: lv.c, align: 'left' });
        }
        for (let i = 0; i + 1 < ys.length; i += 1) {
          g.fills.push({
            pts: [[xL, ys[i].y], [xR, ys[i].y], [xR, ys[i + 1].y], [xL, ys[i + 1].y]],
            color: hexToRgba(ys[i + 1].lv.c, 0.07),
          });
        }
        line(px[0], px[1], { dash: [4, 4], width: 1 });
        break;
      }

      case 'channel': {
        if (px.length < 2) break;
        line(px[0], px[1]);
        if (px.length >= 3) {
          const l0 = this.timeMap.toLogical(points[0].time);
          const l1 = this.timeMap.toLogical(points[1].time);
          const l2 = this.timeMap.toLogical(points[2].time);
          const slope = l1 !== l0 ? (points[1].price - points[0].price) / (l1 - l0) : 0;
          const off = points[2].price - (points[0].price + slope * (l2 - l0));
          const q0 = this.toPx({ time: points[0].time, price: points[0].price + off });
          const q1 = this.toPx({ time: points[1].time, price: points[1].price + off });
          if (q0 && q1) {
            line(q0, q1);
            const m0 = { x: px[0].x, y: (px[0].y + q0.y) / 2 };
            const m1 = { x: px[1].x, y: (px[1].y + q1.y) / 2 };
            line(m0, m1, { dash: [4, 4], width: 1 });
            g.fills.push({ pts: [[px[0].x, px[0].y], [px[1].x, px[1].y], [q1.x, q1.y], [q0.x, q0.y]],
              color: hexToRgba(color, 0.1) });
          }
        }
        break;
      }

      case 'elliott5':
      case 'elliottABC': {
        const names = d.type === 'elliott5' ? ['', '(1)', '(2)', '(3)', '(4)', '(5)'] : ['', '(A)', '(B)', '(C)'];
        for (let i = 0; i + 1 < px.length; i += 1) line(px[i], px[i + 1]);
        for (let i = 1; i < px.length; i += 1) {
          const isHigh = points[i].price >= points[i - 1].price; // 직전 점보다 높으면 고점
          g.labels.push({ x: px[i].x, y: isHigh ? px[i].y - 8 : px[i].y + 16, text: names[i], color,
            align: 'center', bold: true });
        }
        if (d.type === 'elliott5' && points.length === 6) {
          const msgs = checkImpulse(points);
          if (msgs.length) {
            const p5 = px[5];
            const up = points[5].price >= points[4].price;
            msgs.forEach((m, k) => g.labels.push({
              x: p5.x + 8, y: (up ? p5.y - 26 : p5.y + 30) + (up ? -k : k) * 14,
              text: `⚠ ${m}`, color: '#ff5252', align: 'left',
            }));
          }
        }
        break;
      }
      default:
        break;
    }
    return g;
  }

  // ------------------------------------------------------------ 렌더링
  render(ctx, mediaSize) {
    this.size = mediaSize;
    const items = this.drawings.map((d) => ({ d, sel: d.id === this.selectedId, hov: d.id === this.hoverId }));
    if (this.draft) {
      const pts = this.draft.hover ? [...this.draft.points, this.draft.hover] : this.draft.points;
      if (pts.length) items.push({ d: { ...this.draft, points: pts, style: {} }, sel: true, hov: false, draft: true });
    }
    for (const it of items) {
      const g = this.geometry(it.d);
      if (!g) continue;
      ctx.save();
      for (const f of g.fills) {
        ctx.fillStyle = f.color;
        ctx.beginPath();
        f.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fill();
      }
      for (const l of g.lines) {
        ctx.strokeStyle = l.color;
        ctx.lineWidth = l.width + (it.hov ? 1 : 0);
        ctx.setLineDash(l.dash || []);
        ctx.beginPath();
        ctx.moveTo(l.x1, l.y1);
        ctx.lineTo(l.x2, l.y2);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      for (const t of g.labels) {
        ctx.font = `${t.bold ? '600 ' : ''}11px -apple-system, "Segoe UI", "Malgun Gothic", sans-serif`;
        ctx.textAlign = t.align || 'left';
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(19,23,34,0.85)'; // 캔들 위에서도 읽히도록 배경색 테두리
        ctx.strokeText(t.text, t.x, t.y);
        ctx.fillStyle = t.color;
        ctx.fillText(t.text, t.x, t.y);
      }
      if (it.sel) {
        for (const a of g.anchors) {
          ctx.beginPath();
          ctx.arc(a.x, a.y, ANCHOR_R, 0, Math.PI * 2);
          ctx.fillStyle = '#131722';
          ctx.fill();
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = g.color;
          ctx.stroke();
        }
      }
      ctx.restore();
    }
  }

  // ------------------------------------------------------------ 클릭 판정
  hitTest(x, y) {
    if (!this.size.width) this.size = this._paneSize();
    // 선택된 선을 먼저, 나중에 그린 선을 먼저 검사합니다
    const order = [...this.drawings].reverse().sort((a, b) =>
      (b.id === this.selectedId) - (a.id === this.selectedId));
    for (const d of order) {
      const g = this.geometry(d);
      if (!g) continue;
      if (d.id === this.selectedId) {
        const ai = g.anchors.findIndex((a) => Math.hypot(a.x - x, a.y - y) <= ANCHOR_R + 3);
        if (ai >= 0) return { id: d.id, anchor: ai };
      }
      for (const l of g.lines) {
        if (distToSegment(x, y, l.x1, l.y1, l.x2, l.y2) <= HIT_PX) return { id: d.id, anchor: -1 };
      }
    }
    return null;
  }

  // ------------------------------------------------------------ 이벤트
  _insidePane(x, y) {
    const { width, height } = this._paneSize();
    return x >= 0 && y >= 0 && x < width && y < height;
  }

  _bindEvents() {
    const el = this.container;

    // capture 단계에서 먼저 받아, 선을 다루는 동안에는 차트가 화면을 끌지 않게 합니다.
    el.addEventListener('mousedown', (ev) => {
      if (ev.button !== 0) return;
      const { x, y } = this._localXY(ev);
      if (!this._insidePane(x, y)) return;

      if (this.tool !== 'cursor') {
        ev.stopPropagation();
        ev.preventDefault();
        this._addDraftPoint(x, y);
        return;
      }

      const hit = this.hitTest(x, y);
      if (!hit) {
        if (this.selectedId) this.select(null);
        return; // 빈 곳은 차트 이동
      }
      ev.stopPropagation();
      ev.preventDefault();
      if (hit.id !== this.selectedId) this.select(hit.id);
      const d = this.selected;
      const start = this.fromPx(x, y, false);
      if (!start) return;
      this.drag = {
        id: d.id,
        anchor: hit.anchor,
        start,
        startLogical: this.chart.timeScale().coordinateToLogical(x),
        orig: JSON.parse(JSON.stringify(d.points)),
        moved: false,
      };
      this.container.style.cursor = hit.anchor >= 0 ? 'grabbing' : 'move';
    }, true);

    el.addEventListener('mousemove', (ev) => {
      if (this.drag) return;
      const { x, y } = this._localXY(ev);
      if (this.draft) {
        const pt = this._inside(x, y) ? this.fromPx(x, y) : null;
        if (pt) {
          this.draft.hover = pt;
          this.update();
        }
        return;
      }
      if (this.tool === 'cursor') {
        const hit = this._insidePane(x, y) ? this.hitTest(x, y) : null;
        const id = hit ? hit.id : null;
        el.style.cursor = hit ? (hit.anchor >= 0 ? 'grab' : 'pointer') : '';
        if (id !== this.hoverId) {
          this.hoverId = id;
          this.update();
        }
      }
    });

    window.addEventListener('mousemove', (ev) => {
      if (!this.drag) return;
      const { x, y } = this._localXY(ev);
      const d = this.drawings.find((z) => z.id === this.drag.id);
      if (!d) return;
      if (!this.drag.moved) {
        this._snapshot();
        this.drag.moved = true;
      }
      if (this.drag.anchor >= 0) {
        const pt = this.fromPx(x, y);
        if (pt) d.points[this.drag.anchor] = pt;
      } else {
        const lgNow = this.chart.timeScale().coordinateToLogical(x);
        const priceNow = this.series.coordinateToPrice(y);
        if (lgNow === null || priceNow === null) return;
        const dl = Math.round(lgNow - this.drag.startLogical);
        const dp = priceNow - this.drag.start.price;
        d.points = this.drag.orig.map((p) => ({
          time: this.timeMap.toTime(Math.round(this.timeMap.toLogical(p.time)) + dl),
          price: p.price + dp,
        }));
      }
      this.update();
    });

    window.addEventListener('mouseup', () => {
      if (!this.drag) return;
      const moved = this.drag.moved;
      this.drag = null;
      this.container.style.cursor = '';
      if (moved) this.onChange();
    });

    window.addEventListener('keydown', (ev) => {
      const t = ev.target || {};
      const typing = t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable
        || (t.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'color'].includes(t.type));
      if (typing) return;
      if (ev.key === 'Escape') {
        if (this.draft || this.tool !== 'cursor') this.setTool('cursor');
        else this.select(null);
        return;
      }
      if (ev.key === 'Delete' || ev.key === 'Backspace') {
        if (this.selectedId) {
          ev.preventDefault();
          this.deleteSelected();
        }
        return;
      }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') {
        ev.preventDefault();
        this.undo();
        return;
      }
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const tool = Object.entries(TOOLS).find(([, t]) => t.key === ev.key.toLowerCase());
      if (tool) this.setTool(tool[0]);
    });
  }

  _inside(x, y) {
    return this._insidePane(x, y);
  }

  _addDraftPoint(x, y) {
    const pt = this.fromPx(x, y);
    if (!pt) return;
    const spec = TOOLS[this.tool];
    if (!this.draft) this.draft = { type: this.tool, points: [], hover: null };
    this.draft.points.push(pt);
    this.draft.hover = null;
    if (this.draft.points.length >= spec.points) {
      this._snapshot();
      const d = { id: uid(), type: this.draft.type, points: this.draft.points, style: { color: spec.color, width: 1.5 } };
      this.drawings.push(d);
      this.draft = null;
      this.setTool('cursor');
      this.select(d.id);
      this.onChange();
    } else {
      this.update();
    }
  }
}
