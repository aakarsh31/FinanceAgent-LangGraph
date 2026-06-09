const EQUITY_NODES = [
  { id: 'data_fetch',    label: 'DATA FETCH',      role: 'Data Layer',        wave: 0 },
  { id: 'macro',         label: 'MACRO REGIME',    role: 'FRED · Macro',      wave: 1 },
  { id: 'fundamentals',  label: 'FUNDAMENTALS',    role: 'Equity Analysis',   wave: 2 },
  { id: 'sentiment',     label: 'SENTIMENT',       role: 'Equity Analysis',   wave: 2 },
  { id: 'risk',          label: 'RISK MANAGER',    role: 'Equity Analysis',   wave: 2 },
  { id: 'technical',     label: 'TECHNICAL',       role: 'Price Action',      wave: 2 },
  { id: 'bull',          label: 'BULL ANALYST',    role: 'Equity Research',   wave: 3 },
  { id: 'bear',          label: 'BEAR ANALYST',    role: 'Equity Research',   wave: 3 },
  { id: 'valuation',     label: 'VALUATION',       role: 'Equity Research',   wave: 3 },
  { id: 'supervisor',    label: 'SUPERVISOR',      role: 'Portfolio Manager', wave: 4 },
];

const CRYPTO_NODES = [
  { id: 'data_fetch',    label: 'DATA FETCH',      role: 'Data Layer',        wave: 0 },
  { id: 'macro',         label: 'MACRO REGIME',    role: 'FRED · Macro',      wave: 1 },
  { id: 'onchain',       label: 'ON-CHAIN',        role: 'Crypto · On-Chain', wave: 2 },
  { id: 'sentiment',     label: 'SENTIMENT',       role: 'Crypto Analysis',   wave: 2 },
  { id: 'risk',          label: 'RISK MANAGER',    role: 'Crypto Analysis',   wave: 2 },
  { id: 'supervisor',    label: 'SUPERVISOR',      role: 'Portfolio Manager', wave: 4 },
];

function AgentNode({ node, status, output }) {
  const isRunning  = status === 'running';
  const isDone     = status === 'done';

  const borderColor = isDone ? 'var(--green)' : isRunning ? 'var(--amber)' : 'var(--border)';
  const bgColor     = isDone ? 'rgba(34,197,94,0.04)' : isRunning ? 'rgba(245,158,11,0.04)' : 'var(--surface2)';

  return (
    <div style={{
      background: bgColor,
      border: `1px solid ${borderColor}`,
      borderRadius: '6px',
      padding: '10px 14px',
      minWidth: '140px',
      maxWidth: '180px',
      position: 'relative',
      overflow: 'hidden',
      transition: 'all 0.3s ease',
    }}>
      {isRunning && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: '2px',
          background: 'linear-gradient(90deg, transparent, var(--amber), transparent)',
          animation: 'slide 1.5s ease-in-out infinite',
        }} />
      )}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
        <span style={{ fontSize: '9px', color: 'var(--muted)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
          {node.role}
        </span>
        <span style={{
          width: '6px', height: '6px', borderRadius: '50%',
          background: isDone ? 'var(--green)' : isRunning ? 'var(--amber)' : 'var(--dim)',
          transition: 'background 0.3s',
        }} className={isRunning ? 'pulse' : ''} />
      </div>
      <div style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.08em', fontFamily: 'var(--mono)', color: isDone ? 'var(--white)' : 'var(--muted)' }}>
        {node.label}
      </div>
      {output && (
        <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '6px', fontFamily: 'var(--mono)' }}>
          {output}
        </div>
      )}
    </div>
  );
}

function WaveRow({ nodes, nodeStates }) {
  return (
    <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
      {nodes.map(n => (
        <AgentNode key={n.id} node={n} status={nodeStates[n.id] || 'idle'} output={nodeStates[`${n.id}_output`]} />
      ))}
    </div>
  );
}

function Connector({ active, done }) {
  return (
    <div style={{
      width: '2px', height: '20px', margin: '0 auto',
      background: done ? 'var(--green)' : active ? 'var(--amber)' : 'var(--border)',
      transition: 'background 0.4s',
    }} />
  );
}

export default function Pipeline({ assetClass, nodeStates = {}, isPaused }) {
  const nodes  = assetClass === 'crypto' ? CRYPTO_NODES : EQUITY_NODES;
  const waves  = [...new Set(nodes.map(n => n.wave))].sort();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0 }}>
      <style>{`
        @keyframes slide {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
      `}</style>

      {waves.map((wave, wi) => {
        const waveNodes = nodes.filter(n => n.wave === wave);
        const prevDone  = wi === 0 ? false : nodes.filter(n => n.wave === waves[wi - 1]).every(n => nodeStates[n.id] === 'done');
        const thisDone  = waveNodes.every(n => nodeStates[n.id] === 'done');

        return (
          <div key={wave} style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
            {wi > 0 && <Connector active={prevDone && !thisDone} done={thisDone} />}
            <WaveRow nodes={waveNodes} nodeStates={nodeStates} />
          </div>
        );
      })}

      {isPaused && (
        <div style={{
          marginTop: '16px',
          padding: '12px 20px',
          background: 'var(--amber-dim)',
          border: '1px solid var(--amber)',
          borderRadius: '6px',
          fontSize: '12px',
          color: 'var(--amber)',
          fontWeight: 600,
          letterSpacing: '0.06em',
          textAlign: 'center',
        }}>
          ⏸ AWAITING PORTFOLIO MANAGER REVIEW
        </div>
      )}
    </div>
  );
}