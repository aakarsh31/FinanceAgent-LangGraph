import { useState, useRef, useCallback, useEffect } from 'react';
import Pipeline from '../components/Pipeline';
import Report from '../components/Report';
import { AssetBadge } from '../components/Badge';
import { api } from '../lib/api';
import { genThreadId } from '../lib/utils';

const TIMEFRAMES = ['1mo', '3mo', '6mo', '1y'];

// Maps node ids → human label for the live status line
const NODE_LABELS = {
  data_fetch:   'Fetching market data',
  macro:        'Analysing macro regime',
  fundamentals: 'Running fundamentals',
  sentiment:    'Scoring sentiment',
  risk:         'Calculating risk',
  technical:    'Reading technicals',
  onchain:      'Pulling on-chain data',
  bull:         'Building bull case',
  bear:         'Building bear case',
  valuation:    'Valuing the asset',
  supervisor:   'Supervisor writing memo',
};

export default function Analyze() {
  const [ticker, setTicker]             = useState('');
  const [timeframe, setTimeframe]       = useState('3mo');
  const [running, setRunning]           = useState(false);
  const [paused, setPaused]             = useState(false);
  const [approved, setApproved]         = useState(false);
  const [approving, setApproving]       = useState(false);
  const [error, setError]               = useState(null);
  const [assetClass, setAssetClass]     = useState(null);
  const [nodeStates, setNodeStates]     = useState({});
  const [report, setReport]             = useState(null);
  const [intermediate, setIntermediate] = useState(null);
  const [tradeResult, setTradeResult]   = useState(null);
  const [statusLine, setStatusLine]     = useState('');
  const threadRef   = useRef(null);
  const cleanupRef  = useRef(null);  // holds SSE close fn

  // Clean up SSE on unmount
  useEffect(() => () => cleanupRef.current?.(), []);

  const updateNodes = useCallback((updates) => {
    setNodeStates(prev => ({ ...prev, ...updates }));
  }, []);

  const handleAnalyze = () => {
    if (!ticker.trim()) return;

    // Close any existing stream
    cleanupRef.current?.();

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
    setAssetClass(null);
    setStatusLine('Connecting...');

    // Mark data_fetch as running immediately so something lights up right away
    updateNodes({ data_fetch: 'running' });

    cleanupRef.current = api.analyzeStream(t, timeframe, threadId, {

      onNode: (nodeId, data) => {
        // Mark the just-completed node done
        const nodeUpdates = { [nodeId]: 'done' };

        // Extract display snippet per node
        if (nodeId === 'data_fetch' && data.asset_class) {
          setAssetClass(data.asset_class);
        }
        if (nodeId === 'macro' && data.regime_label) {
          nodeUpdates['macro_output'] = data.regime_label;
        }
        if (nodeId === 'sentiment' && data.label) {
          nodeUpdates['sentiment_output'] = data.label;
        }
        if (nodeId === 'risk' && data.volatility != null) {
          nodeUpdates['risk_output'] = `Vol ${parseFloat(data.volatility).toFixed(1)}%`;
        }
        if (nodeId === 'technical' && data.signal) {
          nodeUpdates['technical_output'] = data.signal;
        }
        if (nodeId === 'bull' && data.confidence) {
          nodeUpdates['bull_output'] = `Bull ${data.confidence}`;
        }
        if (nodeId === 'bear' && data.confidence) {
          nodeUpdates['bear_output'] = `Bear ${data.confidence}`;
        }
        if (nodeId === 'valuation' && data.label) {
          nodeUpdates['valuation_output'] = data.label;
        }
        if (nodeId === 'onchain' && data.network_health) {
          nodeUpdates['onchain_output'] = data.network_health;
        }
        if (nodeId === 'supervisor' && data.recommendation) {
          nodeUpdates['supervisor_output'] = data.recommendation;
        }

        updateNodes(nodeUpdates);
        setStatusLine(NODE_LABELS[nodeId] ? `✓ ${NODE_LABELS[nodeId]}` : '');
      },

      onDone: (result) => {
        setAssetClass(result.asset_class);
        setIntermediate(result.intermediate);

        // Fill in any node states from the final result in case an event was missed
        const fills = { data_fetch: 'done', supervisor: 'done' };
        const im = result.intermediate || {};
        if (im.macro)        fills['macro']        = 'done';
        if (im.fundamentals) fills['fundamentals']  = 'done';
        if (im.sentiment)    fills['sentiment']     = 'done';
        if (im.risk)         fills['risk']          = 'done';
        if (im.technical)    fills['technical']     = 'done';
        if (im.onchain)      fills['onchain']       = 'done';
        if (im.bull_thesis)  fills['bull']          = 'done';
        if (im.bear_thesis)  fills['bear']          = 'done';
        if (im.valuation)    fills['valuation']     = 'done';

        if (result.supervisor_report) {
          fills['supervisor_output'] = result.supervisor_report.recommendation;
        }
        updateNodes(fills);

        setReport(result.supervisor_report);
        setStatusLine('');
        setRunning(false);
        setPaused(true);
      },

      onError: (detail) => {
        setError(detail);
        setStatusLine('');
        setRunning(false);
      },
    });
  };

  const handleApprove = async () => {
    if (!threadRef.current) return;
    setApproving(true);
    try {
      const res = await api.approve(threadRef.current);
      setReport(res.supervisor_report);
      setTradeResult(res.trade);
      setPaused(false);
      setApproved(true);
    } catch {
      setError('Approval failed');
    } finally {
      setApproving(false);
    }
  };

  const handleReset = () => {
    cleanupRef.current?.();
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
    setStatusLine('');
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
              width: '100%', height: '42px', borderRadius: '6px',
              cursor: running || !ticker.trim() ? 'not-allowed' : 'pointer',
              background: running ? 'var(--surface3)' : 'var(--blue)', border: 'none',
              color: running ? 'var(--muted)' : 'white', fontFamily: 'var(--mono)',
              fontSize: '12px', fontWeight: 700, letterSpacing: '0.12em',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
              opacity: !ticker.trim() ? 0.5 : 1,
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
                width: '100%', height: '42px', borderRadius: '6px',
                cursor: approving ? 'not-allowed' : 'pointer',
                background: 'transparent', border: '1px solid var(--green)',
                color: 'var(--green)', fontFamily: 'var(--mono)',
                fontSize: '12px', fontWeight: 700, letterSpacing: '0.12em',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                marginTop: '8px',
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
                fontSize: '11px', letterSpacing: '0.1em', marginTop: '8px',
              }}
            >
              ← NEW ANALYSIS
            </button>
          )}
        </div>

        {/* Live status line */}
        {statusLine && (
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', fontSize: '11px', color: 'var(--amber)', fontFamily: 'var(--mono)', letterSpacing: '0.05em' }}>
            {statusLine}
          </div>
        )}

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

        {error && (
          <div style={{
            margin: '16px 24px 0', padding: '12px 16px',
            background: 'var(--red-dim)', border: '1px solid var(--red)',
            borderRadius: '6px', color: 'var(--red)', fontSize: '12px',
          }}>
            {error}
          </div>
        )}

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