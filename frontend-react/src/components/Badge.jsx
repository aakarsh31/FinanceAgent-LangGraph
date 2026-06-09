import { recColor, confColor, signalColor } from '../lib/utils';

export function RecBadge({ rec }) {
  const color = recColor(rec);
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '4px 12px',
      borderRadius: '4px',
      border: `1px solid ${color}`,
      background: `${color}18`,
      color,
      fontSize: '13px',
      fontWeight: 700,
      letterSpacing: '0.08em',
      fontFamily: 'var(--mono)',
    }}>
      {rec || '—'}
    </span>
  );
}

export function ConfBadge({ conf }) {
  const color = confColor(conf);
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '5px',
      padding: '3px 8px',
      borderRadius: '3px',
      background: `${color}18`,
      color,
      fontSize: '11px',
      fontWeight: 600,
      letterSpacing: '0.06em',
      fontFamily: 'var(--mono)',
    }}>
      <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: color, display: 'inline-block' }} />
      {conf || '—'}
    </span>
  );
}

export function SignalBadge({ signal }) {
  const color = signalColor(signal);
  return (
    <span style={{
      padding: '2px 8px',
      borderRadius: '3px',
      background: `${color}18`,
      color,
      fontSize: '11px',
      fontWeight: 600,
      fontFamily: 'var(--mono)',
    }}>
      {signal || '—'}
    </span>
  );
}

export function AssetBadge({ assetClass }) {
  const isEquity = assetClass === 'equity';
  return (
    <span style={{
      padding: '2px 8px',
      borderRadius: '3px',
      background: isEquity ? 'var(--green-dim)' : 'var(--amber-dim)',
      color: isEquity ? 'var(--green)' : 'var(--amber)',
      fontSize: '10px',
      fontWeight: 600,
      letterSpacing: '0.1em',
      fontFamily: 'var(--mono)',
      textTransform: 'uppercase',
    }}>
      {assetClass || '—'}
    </span>
  );
}