export function Card({ children, className = '', style = {} }) {
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: '8px',
      padding: '20px',
      ...style,
    }} className={className}>
      {children}
    </div>
  );
}

export function CardHeader({ label, sub, action }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
      <div>
        <div style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--muted)' }}>
          {label}
        </div>
        {sub && <div style={{ fontSize: '11px', color: 'var(--dim)', marginTop: '2px' }}>{sub}</div>}
      </div>
      {action}
    </div>
  );
}