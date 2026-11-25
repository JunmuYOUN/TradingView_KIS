// 타임프레임 규칙 (server/bars.py 의 bucket_start 와 같아야 합니다).
// 시간은 'KST 벽시계 시각을 UTC epoch 로 인코딩한 초' 입니다.

export const TIMEFRAMES = [
  { id: '1', label: '1분' },
  { id: '3', label: '3분' },
  { id: '5', label: '5분' },
  { id: '15', label: '15분' },
  { id: '30', label: '30분' },
  { id: '60', label: '60분' },
  { id: 'D', label: '일' },
  { id: 'W', label: '주' },
  { id: 'M', label: '월' },
];

export const isMinuteTf = (tf) => /^\d+$/.test(tf);

export function tfSeconds(tf) {
  if (isMinuteTf(tf)) return Number(tf) * 60;
  if (tf === 'D') return 86400;
  if (tf === 'W') return 7 * 86400;
  return 30.4375 * 86400;
}

export function bucketStart(t, tf) {
  if (isMinuteTf(tf)) {
    const size = Number(tf) * 60;
    return t - (t % size);
  }
  const day = t - (t % 86400);
  if (tf === 'D') return day;
  const d = new Date(day * 1000);
  if (tf === 'W') {
    const wd = (d.getUTCDay() + 6) % 7; // 월요일 = 0
    return day - wd * 86400;
  }
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000;
}
