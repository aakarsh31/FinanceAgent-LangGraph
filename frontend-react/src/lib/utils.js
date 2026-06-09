export function fmt(val, suffix = '') {
  if (val == null) return '—';
  return `${val}${suffix}`;
}

export function fmtPct(val) {
  if (val == null) return '—';
  const n = parseFloat(val);
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

export function fmtUsd(val) {
  if (val == null) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
}

export function recColor(rec) {
  if (!rec) return 'var(--muted)';
  const r = rec.toLowerCase();
  if (r === 'buy')  return 'var(--green)';
  if (r === 'sell') return 'var(--red)';
  return 'var(--amber)';
}

export function confColor(conf) {
  if (!conf) return 'var(--muted)';
  const c = conf.toLowerCase();
  if (c === 'high')   return 'var(--green)';
  if (c === 'medium') return 'var(--amber)';
  return 'var(--red)';
}

export function signalColor(sig) {
  if (!sig) return 'var(--muted)';
  const s = sig.toLowerCase();
  if (s === 'bullish') return 'var(--green)';
  if (s === 'bearish') return 'var(--red)';
  return 'var(--amber)';
}

export function genThreadId(ticker) {
  return `${ticker.toLowerCase()}-${Date.now()}`;
}