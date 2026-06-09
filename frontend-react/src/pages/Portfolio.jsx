import { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { Card, CardHeader } from '../components/Card';
import { fmtUsd, fmtPct } from '../lib/utils';
import { api } from '../lib/api';

function StatCard({ label, value, sub, color }) {
  return (
    <Card style={{ flex: 1, minWidth: '140px' }}>
      <div style={{ fontSize: '10px', color: 'var(--muted)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '8px' }}>{label}</div>
      <div style={{ fontSize: '22px', fontWeight: 700, fontFamily: 'var(--mono)', color: color || 'var(--white)' }}>{value}</div>
      {sub && <div style={{ fontSize: '11px', color: 'var(--dim)', marginTop: '4px' }}>{sub}</div>}
    </Card>
  );
}

function PnlColor(val) {
  if (val == null) return 'var(--white)';
  return val >= 0 ? 'var(--green)' : 'var(--red)';
}

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload?.length) {
    return (
      <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: '6px', padding: '10px 14px' }}>
        <div style={{ fontSize: '11px', color: 'var(--muted)', marginBottom: '4px' }}>{label}</div>
        <div style={{ fontSize: '14px', fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--blue)' }}>
          {fmtUsd(payload[0].value)}
        </div>
      </div>
    );
  }
  return null;
};

export default function Portfolio() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      setLoading(true);
      const res = await api.portfolio();
      if (res.status === 'ok') setData(res);
      else setError(res.detail || 'Failed to load portfolio');
    } catch (e) {
      setError('Cannot connect to API');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh', color: 'var(--muted)', fontSize: '13px' }}>
      Loading portfolio...
    </div>
  );

  if (error) return (
    <div style={{ padding: '24px' }}>
      <Card style={{ background: 'var(--red-dim)', border: '1px solid var(--red)', color: 'var(--red)', fontSize: '13px' }}>
        {error}
      </Card>
    </div>
  );

  const { account, positions, equity_curve, recent_orders, stats } = data;

  // Build chart data
  const chartData = (equity_curve?.timestamps || [])
    .map((ts, i) => ({
      date: new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      equity: equity_curve.equity[i],
    }))
    .filter(d => d.equity > 0);

  return (
    <div style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px' }}>
        <div>
          <h1 className="prism" style={{ fontSize: '22px', fontWeight: 700, letterSpacing: '0.04em' }}>
            Paper Portfolio
          </h1>
          <div style={{ fontSize: '12px', color: 'var(--muted)', marginTop: '4px' }}>
            Alpaca Paper Trading · {stats?.open_positions} open positions · {stats?.trades_placed} trades placed
          </div>
        </div>
        <button
          onClick={load}
          style={{
            padding: '8px 16px', background: 'transparent', border: '1px solid var(--border)',
            borderRadius: '6px', color: 'var(--muted)', fontSize: '12px', cursor: 'pointer',
            fontFamily: 'var(--mono)', letterSpacing: '0.06em',
          }}
        >
          ↻ REFRESH
        </button>
      </div>

      {/* Stat cards */}
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '20px' }}>
        <StatCard label="Portfolio Value" value={fmtUsd(account.portfolio_value)} />
        <StatCard label="Cash" value={fmtUsd(account.cash)} />
        <StatCard
          label="Day P&L"
          value={fmtUsd(account.day_pnl)}
          sub={fmtPct(account.day_pnl_pct)}
          color={PnlColor(account.day_pnl)}
        />
        <StatCard
          label="Total P&L"
          value={fmtUsd(account.total_pnl)}
          sub={fmtPct(account.total_pnl_pct)}
          color={PnlColor(account.total_pnl)}
        />
      </div>

      {/* Equity curve */}
      {chartData.length > 1 && (
        <Card style={{ marginBottom: '20px' }}>
          <CardHeader label="Equity Curve" sub="1 Month" />
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={v => `$${(v/1000).toFixed(0)}k`} />
              <Tooltip content={<CustomTooltip />} />
              <Line type="monotone" dataKey="equity" stroke="var(--blue)" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Positions */}
      <Card style={{ marginBottom: '20px' }}>
        <CardHeader label="Open Positions" sub={`${positions.length} positions`} />
        {positions.length === 0 ? (
          <div style={{ color: 'var(--dim)', fontSize: '13px', textAlign: 'center', padding: '20px' }}>No open positions</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Ticker', 'Qty', 'Entry', 'Current', 'Market Value', 'Unrealized P&L', 'Today'].map(h => (
                  <th key={h} style={{ textAlign: 'left', fontSize: '10px', color: 'var(--muted)', letterSpacing: '0.1em', textTransform: 'uppercase', padding: '0 8px 10px', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map(p => (
                <tr key={p.ticker} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 8px', fontWeight: 700, fontFamily: 'var(--mono)', fontSize: '13px' }}>{p.ticker}</td>
                  <td style={{ padding: '10px 8px', fontFamily: 'var(--mono)', fontSize: '12px', color: 'var(--muted)' }}>{p.qty?.toFixed(4)}</td>
                  <td style={{ padding: '10px 8px', fontFamily: 'var(--mono)', fontSize: '12px' }}>{fmtUsd(p.avg_entry_price)}</td>
                  <td style={{ padding: '10px 8px', fontFamily: 'var(--mono)', fontSize: '12px' }}>{fmtUsd(p.current_price)}</td>
                  <td style={{ padding: '10px 8px', fontFamily: 'var(--mono)', fontSize: '12px' }}>{fmtUsd(p.market_value)}</td>
                  <td style={{ padding: '10px 8px', fontFamily: 'var(--mono)', fontSize: '12px', color: PnlColor(p.unrealized_pl) }}>
                    {fmtUsd(p.unrealized_pl)} ({fmtPct(p.unrealized_plpc)})
                  </td>
                  <td style={{ padding: '10px 8px', fontFamily: 'var(--mono)', fontSize: '12px', color: PnlColor(p.change_today) }}>
                    {fmtPct(p.change_today)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Recent orders */}
      <Card>
        <CardHeader label="Recent Orders" sub={`${recent_orders.length} orders`} />
        {recent_orders.length === 0 ? (
          <div style={{ color: 'var(--dim)', fontSize: '13px', textAlign: 'center', padding: '20px' }}>No orders</div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['Ticker', 'Side', 'Type', 'Qty / Notional', 'Avg Fill', 'Status', 'Submitted'].map(h => (
                  <th key={h} style={{ textAlign: 'left', fontSize: '10px', color: 'var(--muted)', letterSpacing: '0.1em', textTransform: 'uppercase', padding: '0 8px 10px', fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recent_orders.map(o => (
                <tr key={o.order_id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 8px', fontWeight: 700, fontFamily: 'var(--mono)', fontSize: '13px' }}>{o.ticker}</td>
                  <td style={{ padding: '10px 8px' }}>
                    <span style={{ fontSize: '11px', fontWeight: 600, fontFamily: 'var(--mono)', color: o.side === 'buy' ? 'var(--green)' : 'var(--red)' }}>
                      {o.side?.toUpperCase()}
                    </span>
                  </td>
                  <td style={{ padding: '10px 8px', fontSize: '11px', color: 'var(--muted)', fontFamily: 'var(--mono)' }}>{o.type}</td>
                  <td style={{ padding: '10px 8px', fontSize: '12px', fontFamily: 'var(--mono)' }}>
                    {o.notional ? fmtUsd(o.notional) : o.qty ? o.qty.toFixed(4) : '—'}
                  </td>
                  <td style={{ padding: '10px 8px', fontSize: '12px', fontFamily: 'var(--mono)' }}>{o.filled_avg_price ? fmtUsd(o.filled_avg_price) : '—'}</td>
                  <td style={{ padding: '10px 8px' }}>
                    <span style={{
                      fontSize: '10px', fontWeight: 600, fontFamily: 'var(--mono)', padding: '2px 6px', borderRadius: '3px',
                      background: o.status === 'filled' ? 'var(--green-dim)' : o.status === 'canceled' ? 'var(--red-dim)' : 'var(--amber-dim)',
                      color: o.status === 'filled' ? 'var(--green)' : o.status === 'canceled' ? 'var(--red)' : 'var(--amber)',
                    }}>
                      {o.status}
                    </span>
                  </td>
                  <td style={{ padding: '10px 8px', fontSize: '11px', color: 'var(--muted)' }}>
                    {o.submitted_at ? new Date(o.submitted_at).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}