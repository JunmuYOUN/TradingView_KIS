// 선의 좌표는 (time, price) 로 저장합니다. 화면에 그릴 때는 time 을 차트의 logical index
// (봉 번호, 소수 가능)로 바꿔야 하므로, 현재 봉 목록을 기준으로 양방향 변환을 제공합니다.
// 데이터 범위 밖(미래 영역 포함)은 타임프레임 간격으로 외삽합니다.

const DAY = 86400;
const weekday = (t) => (Math.floor(t / DAY) + 3) % 7; // 0 = 월요일 (1970-01-01 은 목요일)

function addBusinessDays(t, n) {
  let x = t;
  let k = n;
  while (k > 0) {
    x += DAY;
    if (weekday(x) < 5) k -= 1;
  }
  return x;
}

function businessDaysBetween(a, b) {
  // a < b, 둘 다 자정 기준. a 이후 b 까지의 평일 수
  let n = 0;
  for (let x = a + DAY; x <= b; x += DAY) if (weekday(x) < 5) n += 1;
  return n;
}

export class TimeMap {
  constructor() {
    this.times = [];
    this.step = 60;
    this.businessDays = false;
  }

  setBars(bars, stepSec, businessDays = false) {
    this.times = bars.map((b) => b.time);
    this.step = stepSec;
    this.businessDays = businessDays;
  }

  push(time) {
    const n = this.times.length;
    if (!n || time > this.times[n - 1]) this.times.push(time);
  }

  get length() {
    return this.times.length;
  }

  toLogical(t) {
    const ts = this.times;
    const n = ts.length;
    if (!n) return 0;
    if (t <= ts[0]) return (t - ts[0]) / this.step;
    const last = ts[n - 1];
    if (t >= last) {
      if (this.businessDays) {
        const dayStart = t - (t % DAY);
        return n - 1 + businessDaysBetween(last, dayStart) + (t - dayStart) / DAY;
      }
      return n - 1 + (t - last) / this.step;
    }
    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (ts[mid] <= t) lo = mid; else hi = mid;
    }
    return lo + (t - ts[lo]) / (ts[hi] - ts[lo]);
  }

  toTime(l) {
    const ts = this.times;
    const n = ts.length;
    if (!n) return 0;
    if (l <= 0) return Math.round(ts[0] + l * this.step);
    if (l >= n - 1) {
      const k = l - (n - 1);
      if (this.businessDays) {
        const whole = Math.floor(k);
        return Math.round(addBusinessDays(ts[n - 1], whole) + (k - whole) * DAY);
      }
      return Math.round(ts[n - 1] + k * this.step);
    }
    const i = Math.floor(l);
    return Math.round(ts[i] + (l - i) * (ts[i + 1] - ts[i]));
  }
}
