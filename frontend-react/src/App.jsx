import { useState } from 'react';
import Analyze from './pages/Analyze';
import Portfolio from './pages/Portfolio';
import './index.css';

function NavLink({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: 'none', border: 'none', cursor: 'pointer',
        padding: '4px 12px', borderRadius: '4px',
        color: active ? 'var(--white)' : 'var(--muted)',
        fontSize: '12px', fontWeight: active ? 600 : 400,
        letterSpacing: '0.06em',
        background: active ? 'var(--surface3)' : 'transparent',
        transition: 'all 0.15s',
      }}
    >
      {label}
    </button>
  );
}

export default function App() {
  const [page, setPage] = useState('analyze');
  const [clock, setClock] = useState('');

  // Clock
  useState(() => {
    const tick = () => {
      setClock(new Date().toLocaleTimeString('en-US', {
        timeZone: 'America/New_York', hour12: false,
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      }) + ' ET');
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  });

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>

      {/* Topbar */}
      <header style={{
        background: 'var(--surface)',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 20px', height: '52px',
        position: 'sticky', top: 0, zIndex: 100,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
          <div className="prism" style={{ fontSize: '16px', fontWeight: 800, letterSpacing: '0.06em', fontFamily: 'var(--mono)' }}>
            FINANCEAGENT
          </div>
          <nav style={{ display: 'flex', gap: '4px' }}>
            <NavLink label="Analysis" active={page === 'analyze'} onClick={() => setPage('analyze')} />
            <NavLink label="Portfolio" active={page === 'portfolio'} onClick={() => setPage('portfolio')} />
          </nav>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          <span style={{ fontSize: '11px', color: 'var(--dim)', letterSpacing: '0.06em' }}>
            LANGGRAPH · GPT-4.1 · ALPACA PAPER
          </span>
          <span style={{ fontSize: '12px', color: 'var(--amber)', fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>
            {clock}
          </span>
        </div>
      </header>

      {/* Page content — both stay mounted to preserve state */}
      <div style={{ flex: 1 }}>
        <div style={{ display: page === 'analyze' ? 'block' : 'none' }}>
          <Analyze />
        </div>
        <div style={{ display: page === 'portfolio' ? 'block' : 'none' }}>
          <Portfolio />
        </div>
      </div>

      {/* Footer */}
      <footer style={{
        borderTop: '1px solid var(--border)', padding: '10px 20px',
        background: 'var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: '10px', color: 'var(--dim)', letterSpacing: '0.08em' }}>
          NOT FINANCIAL ADVICE · FOR RESEARCH PURPOSES ONLY
        </span>
        <span style={{ fontSize: '10px', color: 'var(--dim)' }}>
          FinanceAgent-LangGraph · Day 11
        </span>
      </footer>
    </div>
  );
}