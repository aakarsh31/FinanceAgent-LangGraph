import { Card, CardHeader } from './Card';
import { RecBadge, ConfBadge, SignalBadge } from './Badge';
import { fmtUsd } from '../lib/utils';

function Section({ children, style = {} }) {
  return (
    <div className="fade-in" style={{ marginBottom: '12px', ...style }}>
      {children}
    </div>
  );
}

function PanelHead({ label, sub }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
      <span className="prism" style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase' }}>{label}</span>
      {sub && <span style={{ fontSize: '10px', color: 'var(--dim)', letterSpacing: '0.08em' }}>{sub}</span>}
    </div>
  );
}

export default function Report({ report, assetClass, intermediate, onApprove, approving, approved, tradeResult }) {
  if (!report) return null;

  const rec  = report.recommendation;
  const conf = report.confidence;

  return (
    <div>
      {/* Header */}
      <Section>
        <Card style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <RecBadge rec={rec} />
            <ConfBadge conf={conf} />
            <span style={{ fontSize: '12px', color: 'var(--muted)', fontFamily: 'var(--mono)' }}>
              {assetClass?.toUpperCase()} · NOT FINANCIAL ADVICE
            </span>
          </div>
          {!approved ? (
            <button
              onClick={onApprove}
              disabled={approving}
              style={{
                padding: '10px 24px',
                background: approving ? 'var(--surface3)' : 'transparent',
                border: '1px solid var(--green)',
                borderRadius: '6px',
                color: 'var(--green)',
                fontSize: '12px',
                fontWeight: 700,
                letterSpacing: '0.1em',
                cursor: approving ? 'not-allowed' : 'pointer',
                fontFamily: 'var(--mono)',
                transition: 'all 0.15s',
              }}
            >
              {approving ? 'APPROVING...' : '✓ APPROVE & TRADE'}
            </button>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ color: 'var(--green)', fontSize: '12px', fontWeight: 600 }}>✓ APPROVED</span>
              {tradeResult?.traded && (
                <span style={{ fontSize: '11px', color: 'var(--muted)', fontFamily: 'var(--mono)' }}>
                  Order {tradeResult.order?.order_id?.slice(0, 8)}...
                </span>
              )}
              {tradeResult?.skipped_reason && (
                <span style={{ fontSize: '11px', color: 'var(--amber)', fontFamily: 'var(--mono)' }}>
                  Trade skipped: {tradeResult.skipped_reason}
                </span>
              )}
            </div>
          )}
        </Card>
      </Section>

      {/* Executive Summary */}
      <Section>
        <Card>
          <PanelHead label="Executive Summary" />
          <p style={{ fontSize: '13px', lineHeight: 1.7, color: 'var(--white)' }}>{report.summary}</p>
        </Card>
      </Section>

      {/* Macro context */}
      {report.macro_context && (
        <Section>
          <Card>
            <PanelHead label="Macro Regime" sub="FRED" />
            {intermediate?.macro?.regime_label && (
              <div style={{
                display: 'inline-flex', padding: '4px 12px', borderRadius: '4px',
                background: 'var(--blue-dim)', color: 'var(--blue)',
                fontSize: '11px', fontWeight: 600, fontFamily: 'var(--mono)',
                marginBottom: '10px',
              }}>
                {intermediate.macro.regime_label}
              </div>
            )}
            <p style={{ fontSize: '13px', lineHeight: 1.7, color: 'var(--muted)' }}>{report.macro_context}</p>
          </Card>
        </Section>
      )}

      {/* Technical Analysis */}
      {intermediate?.technical && (
        <Section>
          <Card>
            <PanelHead label="Technical Analysis" sub="PRICE ACTION" />
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '12px' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <span style={{ fontSize: '10px', color: 'var(--dim)', letterSpacing: '0.1em' }}>SIGNAL</span>
                <SignalBadge signal={intermediate.technical.signal} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <span style={{ fontSize: '10px', color: 'var(--dim)', letterSpacing: '0.1em' }}>TREND</span>
                <SignalBadge signal={intermediate.technical.trend} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <span style={{ fontSize: '10px', color: 'var(--dim)', letterSpacing: '0.1em' }}>MOMENTUM</span>
                <SignalBadge signal={intermediate.technical.momentum} />
              </div>
              {intermediate.technical.atr_pct && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <span style={{ fontSize: '10px', color: 'var(--dim)', letterSpacing: '0.1em' }}>ATR</span>
                  <span style={{ fontSize: '12px', fontFamily: 'var(--mono)', color: 'var(--white)' }}>{intermediate.technical.atr_pct}%</span>
                </div>
              )}
            </div>
            {intermediate.technical.key_levels?.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {intermediate.technical.key_levels.map((l, i) => (
                  <div key={i} style={{ fontSize: '12px', color: 'var(--muted)', fontFamily: 'var(--mono)', display: 'flex', gap: '8px' }}>
                    <span style={{ color: 'var(--dim)' }}>›</span> {l}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </Section>
      )}

      {/* Bull / Bear */}
      <Section>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          <Card>
            <PanelHead label="Bull Case" />
            <p style={{ fontSize: '13px', lineHeight: 1.7, color: 'var(--white)', marginBottom: '12px' }}>{report.bull_case}</p>
          </Card>
          <Card>
            <PanelHead label="Bear Case" />
            <p style={{ fontSize: '13px', lineHeight: 1.7, color: 'var(--white)', marginBottom: '12px' }}>{report.bear_case}</p>
          </Card>
        </div>
      </Section>

      {/* Key Metrics */}
      {report.key_metrics?.length > 0 && (
        <Section>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <Card>
              <PanelHead label="Key Metrics" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {report.key_metrics.map((m, i) => {
                  const [k, v] = m.split(':');
                  return (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                      <span style={{ fontSize: '12px', color: 'var(--muted)' }}>{k?.trim()}</span>
                      <span style={{ fontSize: '12px', fontFamily: 'var(--mono)', fontWeight: 600, color: 'var(--white)' }}>{v?.trim() || '—'}</span>
                    </div>
                  );
                })}
              </div>
            </Card>
            <Card>
              <PanelHead label="Analyst Agreement" />
              <p style={{ fontSize: '13px', lineHeight: 1.7, color: 'var(--muted)' }}>{report.analyst_agreement}</p>
            </Card>
          </div>
        </Section>
      )}
    </div>
  );
}