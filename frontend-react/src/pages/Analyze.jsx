import { useState, useRef, useCallback } from 'react';
import Pipeline from '../components/Pipeline';
import Report from '../components/Report';
import { AssetBadge } from '../components/Badge';
import { api } from '../lib/api';
import { genThreadId } from '../lib/utils';

const TIMEFRAMES = ['1mo', '3mo', '6mo', '1y'];

// Map log messages → node state updates
function parseLogLine(line, assetClass) {
  const output = {};

  if (line.includes('DataFetchAgent complete'))         output['data_fetch'] = 'done';
  else if (line.includes('DataFetchAgent starting'))    output['data_fetch'] = 'running';

  if (line.includes('MacroRegimeAgent complete')) {
    output['macro'] = 'done';
    const m = line.match(/regime='([^']+)'/);
    if (m) output['macro_output'] = m[1];
  } else if (line.includes('MacroRegimeAgent starting')) output['macro'] = 'running';

  if (line.includes('FundamentalsAgent complete')) {
    output['fundamentals'] = 'done';
    const m = line.match(/PE=([\d.]+)/);
    if (m) output['fundamentals_output'] = `P/E ${parseFloat(m[1]).toFixed(1)}`;
  } else if (line.includes('FundamentalsAgent starting')) output['fundamentals'] = 'running';

  if (line.includes('SentimentAgent complete')) {
    output[assetClass === 'crypto' ? 'sentiment' : 'sentiment'] = 'done';
    const m = line.match(/label=(\w+)/);
    if (m) output['sentiment_output'] = m[1];
  } else if (line.includes('SentimentAgent starting')) output['sentiment'] = 'running';

  if (line.includes('RiskDataAgent complete')) {
    output['risk'] = 'done';
    const m = line.match(/volatility=([\d.]+)/);
    if (m) output['risk_output'] = `Vol ${m[1]}%`;
  } else if (line.includes('RiskDataAgent starting')) output['risk'] = 'running';

  if (line.includes('TechnicalAnalyst complete')) {
    output['technical'] = 'done';
    const m = line.match(/signal=(\w+)/);
    if (m) output['technical_output'] = m[1];
  } else if (line.includes('TechnicalAnalyst starting')) output['technical'] = 'running';

  if (line.includes('OnChainAnalyst complete')) {
    output['onchain'] = 'done';
    const m = line.match(/health=(\w+)/);
    if (m) output['onchain_output'] = m[1];
  } else if (line.includes('OnChainAnalyst starting')) output['onchain'] = 'running';

  if (line.includes('BullAnalyst complete')) {
    output['bull'] = 'done';
    const m = line.match(/confidence=(\w+)/);
    if (m) output['bull_output'] = `Bull ${m[1]}`;
  } else if (line.includes('BullAnalyst starting')) output['bull'] = 'running';

  if (line.includes('BearAnalyst complete')) {
    output['bear'] = 'done';
    const m = line.match(/confidence=(\w+)/);
    if (m) output['bear_output'] = `Bear ${m[1]}`;
  } else if (line.includes('BearAnalyst starting')) output['bear'] = 'running';

  if (line.includes('ValuationAnalyst complete')) {
    output['valuation'] = 'done';
    const m = line.match(/label=(\w+)/);
    if (m) output['valuation_output'] = m[1];
  } else if (line.includes('ValuationAnalyst starting')) output['valuation'] = 'running';

  if (line.includes('SupervisorAgent complete')) {
    output['supervisor'] = 'done';
    const m = line.match(/recommendation=(\w+)/);
    if (m) output['supervisor_output'] = m[1];
  } else if (line.includes('SupervisorAgent starting')) output['supervisor'] = 'running';

  return output;
}

export default function Analyze() {
  const [ticker, setTicker]           = useState('');
  const [timeframe, setTimeframe]     = useState('3mo');
  const [running, setRunning]         = useState(false);
  const [paused, setPaused]           = useState(false);
  const [approved, setApproved]       = useState(false);
  const [approving, setApproving]     = useState(false);
  const [error, setError]             = useState(null);
  const [assetClass, setAssetClass]   = useState(null);
  const [nodeStates, setNodeStates]   = useState({});
  const [report, setReport]           = useState(null);
  const [intermediate, setIntermediate] = useState(null);
  const [tradeResult, setTradeResult] = useState(null);
  const [logs, setLogs]               = useState([]);
  const threadRef                     = useRef(null);

  const updateNodes = useCallback((updates) => {
    setNodeStates(prev => ({ ...prev, ...updates }));
  }, []);

  const handleAnalyze = async () => {
    if (!ticker.trim()) return;

    const t = ticker.trim().toUpperCase();
    const threadId = genThreadId(t);
    threadRef.current = threadId;

    setRunning(true);
    setPaused(false);
    setApproved(false);
    setReport(null);
    setIntermediate(null);
    setTradeResult(null);
    setError(null);
    setNodeStates({});
    setLogs([]);
    setAssetClass(null);
    updateNodes({ data_fetch: 'running' });

    try {
      const res = await api.analyze(t, timeframe, threadId);

      if (res.status === 'error') {
        setError(res.message || 'Analysis failed');
        setRunning(false);
        return;
      }

      // Update asset class
      setAssetClass(res.asset_class);

      // Update node states from intermediate data
      if (res.intermediate) {
        setIntermediate(res.intermediate);

        updateNodes({ data_fetch: 'done' });
        if (res.intermediate.macro)        updateNodes({ macro: 'done', macro_output: res.intermediate.macro.regime_label });
        if (res.intermediate.fundamentals) updateNodes({ fundamentals: 'done' });
        if (res.intermediate.sentiment)    updateNodes({ sentiment: 'done', sentiment_output: res.intermediate.sentiment.sentiment_label });
        if (res.intermediate.risk)         updateNodes({ risk: 'done', risk_output: `Vol ${res.intermediate.risk.volatility}%` });
        if (res.intermediate.technical)    updateNodes({ technical: 'done', technical_output: res.intermediate.technical.signal });
        if (res.intermediate.onchain)      updateNodes({ onchain: 'done' });
        if (res.intermediate.bull_thesis)  updateNodes({ bull: 'done', bull_output: `Bull ${res.intermediate.bull_thesis.confidence}` });
        if (res.intermediate.bear_thesis)  updateNodes({ bear: 'done', bear_output: `Bear ${res.intermediate.bear_thesis.confidence}` });
        if (res.intermediate.valuation)    updateNodes({ valuation: 'done', valuation_output: res.intermediate.valuation.valuation_label });
      }

      // Supervisor already ran — show the report immediately
      if (res.supervisor_report) {
        updateNodes({ supervisor: 'done', supervisor_output: res.supervisor_report.recommendation });
        setReport(res.supervisor_report);
      }

      setPaused(true);
      setRunning(false);
    } catch (e) {
      setError('Network error — is the API running?');
      setRunning(false);
    }
  };

  const handleApprove = async () => {
    if (!threadRef.current) return;
    setApproving(true);
    updateNodes({ supervisor: 'running' });

    try {
      const res = await api.approve(threadRef.current);
      updateNodes({ supervisor: 'done', supervisor_output: res.supervisor_report?.recommendation });
      setReport(res.supervisor_report);
      setTradeResult(res.trade);
      setPaused(false);
      setApproved(true);
    } catch (e) {
      setError('Approval failed');
    } finally {
      setApproving(false);
    }
  };

  const handleReset = () => {
    setTicker('');
    setRunning(false);
    setPaused(false);
    setApproved(false);
    setReport(null);
    setIntermediate(null);
    setNodeStates({});
    setError(null);
    setAssetClass(null);
    setTradeResult(null);
    threadRef.current = null;
  };

  const showPipeline = running || paused || Object.keys(nodeStates).length > 0;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', minHeight: 'calc(100vh - 52px)' }}>

      {/* Sidebar */}
      <aside style={{ borderRight: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column' }}>

        {/* Ticker input */}
        <div style={{ padding: '20px 16px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: '10px', color: 'var(--muted)', letterSpacing: '0.15em', textTransform: 'uppercase', marginBottom: '10px' }}>
            Ticker Symbol
          </div>
          <div style={{
            display: 'flex', alignItems: 'center',
            background: 'var(--surface2)', border: '1px solid rgba(99,102,241,0.3)',
            borderRadius: '6px', overflow: 'hidden',
            transition: 'border-color 0.2s, box-shadow 0.2s',
          }}>
            <span style={{ padding: '0 12px', color: 'var(--blue)', fontSize: '14px', fontWeight: 700, borderRight: '1px solid rgba(99,102,241,0.2)', height: '44px', display: 'flex', alignItems: 'center', fontFamily: 'var(--mono)' }}>
              $
            </span>
            <input
              type="text"
              value={ticker}
              onChange={e => setTicker(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === 'Enter' && !running && handleAnalyze()}
              placeholder="AAPL, BTC-USD..."
              maxLength={12}
              style={{
                background: 'transparent', border: 'none', color: 'var(--white)',
                fontFamily: 'var(--mono)', fontSize: '16px', fontWeight: 600,
                letterSpacing: '0.1em', padding: '0 12px', width: '100%',
                height: '44px', outline: 'none',
              }}
            />
          </div>
          {assetClass && (
            <div style={{ marginTop: '8px' }}>
              <AssetBadge assetClass={assetClass} />
            </div>
          )}
        </div>

        {/* Timeframe */}
        <div style={{ padding: '16px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: '10px', color: 'var(--muted)', letterSpacing: '0.15em', textTransform: 'uppercase', marginBottom: '10px' }}>
            Lookback Period
          </div>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {TIMEFRAMES.map(t => (
              <button
                key={t}
                onClick={() => setTimeframe(t)}
                style={{
                  padding: '5px 10px', borderRadius: '4px', cursor: 'pointer',
                  fontFamily: 'var(--mono)', fontSize: '11px', fontWeight: 600,
                  border: timeframe === t ? '1px solid var(--blue)' : '1px solid var(--border)',
                  background: timeframe === t ? 'var(--blue-dim)' : 'var(--surface2)',
                  color: timeframe === t ? 'var(--blue)' : 'var(--muted)',
                  transition: 'all 0.15s',
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div style={{ padding: '16px', borderBottom: '1px solid var(--border)' }}>
          <button
            onClick={handleAnalyze}
            disabled={running || !ticker.trim()}
            style={{
              width: '100%', height: '42px', borderRadius: '6px', cursor: running || !ticker.trim() ? 'not-allowed' : 'pointer',
              background: running ? 'var(--surface3)' : 'var(--blue)', border: 'none',
              color: running ? 'var(--muted)' : 'white', fontFamily: 'var(--mono)',
              fontSize: '12px', fontWeight: 700, letterSpacing: '0.12em',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
              transition: 'all 0.15s', opacity: !ticker.trim() ? 0.5 : 1,
            }}
          >
            {running ? (
              <>
                <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--amber)', display: 'inline-block' }} className="pulse" />
                ANALYZING...
              </>
            ) : '▶ RUN ANALYSIS'}
          </button>

          {paused && (
            <button
              onClick={handleApprove}
              disabled={approving}
              style={{
                width: '100%', height: '42px', borderRadius: '6px', cursor: approving ? 'not-allowed' : 'pointer',
                background: 'transparent', border: '1px solid var(--green)',
                color: 'var(--green)', fontFamily: 'var(--mono)',
                fontSize: '12px', fontWeight: 700, letterSpacing: '0.12em',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                marginTop: '8px', transition: 'all 0.15s',
              }}
            >
              {approving ? 'APPROVING...' : '✓ APPROVE & TRADE'}
            </button>
          )}

          {(report || error) && (
            <button
              onClick={handleReset}
              style={{
                width: '100%', height: '36px', borderRadius: '6px', cursor: 'pointer',
                background: 'transparent', border: '1px solid var(--border)',
                color: 'var(--muted)', fontFamily: 'var(--mono)',
                fontSize: '11px', letterSpacing: '0.1em',
                marginTop: '8px', transition: 'all 0.15s',
              }}
            >
              ← NEW ANALYSIS
            </button>
          )}
        </div>

        {/* Firm roster */}
        <div style={{ padding: '16px', flex: 1 }}>
          <div style={{ fontSize: '10px', color: 'var(--muted)', letterSpacing: '0.15em', textTransform: 'uppercase', marginBottom: '12px' }}>
            The Firm
          </div>
          {[
            'Macro Regime Analyst',
            'Fundamentals Agent',
            'Technical Analyst',
            'Bull Analyst',
            'Bear Analyst',
            'Valuation Analyst',
            'Risk Manager',
            'Sentiment Agent',
            'On-Chain Analyst',
          ].map(a => (
            <div key={a} style={{ fontSize: '11px', color: 'var(--dim)', lineHeight: 2.2 }}>{a}</div>
          ))}
          <div style={{ fontSize: '11px', color: 'var(--amber)', lineHeight: 2.2, marginTop: '4px' }}>Supervisor Agent</div>
          <div style={{ fontSize: '11px', color: 'var(--muted)', lineHeight: 2.2 }}>— HITL Gate —</div>
        </div>
      </aside>

      {/* Main content */}
      <main style={{ background: 'var(--bg)', overflow: 'auto' }}>

        {/* Error */}
        {error && (
          <div style={{
            margin: '16px 24px 0', padding: '12px 16px',
            background: 'var(--red-dim)', border: '1px solid var(--red)',
            borderRadius: '6px', color: 'var(--red)', fontSize: '12px',
          }}>
            {error}
          </div>
        )}

        {/* Pipeline */}
        {showPipeline && (
          <div style={{ padding: '24px', borderBottom: '1px solid var(--border)' }}>
            <div style={{ fontSize: '11px', color: 'var(--muted)', letterSpacing: '0.15em', textTransform: 'uppercase', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span>PIPELINE</span>
              <span style={{ color: 'var(--blue)', fontFamily: 'var(--mono)' }}>{ticker}</span>
              <span style={{ flex: 1, height: '1px', background: 'var(--border)' }} />
            </div>
            <Pipeline
              assetClass={assetClass}
              nodeStates={nodeStates}
              isPaused={paused}
            />
          </div>
        )}

        {/* Report */}
        {report && (
          <div style={{ padding: '24px' }}>
            <Report
              report={report}
              assetClass={assetClass}
              intermediate={intermediate}
              onApprove={handleApprove}
              approving={approving}
              approved={approved}
              tradeResult={tradeResult}
            />
          </div>
        )}

        {/* Empty state */}
        {!showPipeline && !report && !error && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '70vh', gap: '16px' }}>
            <div className="prism" style={{ fontSize: '64px', fontWeight: 800, letterSpacing: '0.02em', lineHeight: 1, textAlign: 'center' }}>
              FINANCE<br/>AGENT
            </div>
            <div style={{ fontSize: '12px', color: 'var(--dim)', letterSpacing: '0.2em', textTransform: 'uppercase' }}>
              Multi-Agent Investment Research Platform
            </div>
            <div style={{ fontSize: '11px', color: 'var(--dim)', marginTop: '8px' }}>
              Enter a ticker symbol to deploy the firm
            </div>
          </div>
        )}
      </main>
    </div>
  );
}