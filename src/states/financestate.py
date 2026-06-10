from typing import TypedDict, Optional
from pydantic import BaseModel, Field


class FundamentalsData(BaseModel):
    PE_ratio: Optional[float] = Field(
        default=None,
        description="Price to Earnings ratio — stock price divided by trailing twelve-month earnings per share"
    )
    EPS: Optional[float] = Field(
        default=None,
        description="Trailing twelve-month Earnings Per Share in USD"
    )
    revenue_growth: Optional[float] = Field(
        default=None,
        description="Year-over-year revenue growth as a decimal e.g. 0.125 means 12.5% growth"
    )
    debt_to_equity: Optional[float] = Field(
        default=None,
        description="Total debt divided by total shareholder equity — higher values indicate more leverage"
    )


class SentimentData(BaseModel):
    sentiment_score: float = Field(
        description="Sentiment score from -1.0 (very bearish) to 1.0 (very bullish)"
    )
    sentiment_label: str = Field(
        description="Classification of the overall sentiment: 'bullish', 'bearish', or 'neutral'"
    )
    sentiment_reasoning: str = Field(
        description="Concise explanation of why this sentiment score and label were assigned based on the headlines"
    )


class RiskData(BaseModel):
    volatility: Optional[float] = Field(
        default=None,
        description="Annualized volatility as a percentage — computed from daily returns std x sqrt(252)"
    )
    beta: Optional[float] = Field(
        default=None,
        description="Beta relative to S&P 500 — 1.0 moves with market, >1.0 more volatile, <1.0 more stable"
    )
    risk_flag: list[str] = Field(
        description="Specific risk flags identified e.g. ['High volatility', 'Elevated beta', 'High debt load']"
    )


# ── Analyst consensus — promoted from raw dict ────────────────────────────────

class AnalystConsensus(BaseModel):
    recommendation: Optional[str] = Field(
        default=None,
        description="Normalised Wall Street consensus: 'Buy', 'Hold', 'Sell', or 'unavailable'"
    )
    target_price: Optional[float] = Field(
        default=None,
        description="Mean analyst price target in USD"
    )
    num_analysts: Optional[int] = Field(
        default=None,
        description="Number of analysts contributing to the consensus"
    )


class MacroRegimeData(BaseModel):
    fed_funds_rate: Optional[float] = Field(
        default=None,
        description="Current Federal Funds effective rate as a percentage e.g. 5.33"
    )
    cpi_yoy: Optional[float] = Field(
        default=None,
        description="Consumer Price Index year-over-year percentage change — measures inflation"
    )
    yield_curve_spread: Optional[float] = Field(
        default=None,
        description="10-year minus 2-year Treasury yield spread in percentage points — negative signals inversion"
    )
    unemployment_rate: Optional[float] = Field(
        default=None,
        description="US unemployment rate as a percentage"
    )
    regime_label: str = Field(
        description="Short macro regime classification e.g. 'Risk-Off Tightening', 'Risk-On Easing', 'Stagflation', 'Early Recovery'"
    )
    regime_summary: str = Field(
        description="2-3 sentence narrative explaining the current macro environment and its implication for markets"
    )


class BullThesis(BaseModel):
    thesis: str = Field(
        description="Full bull case narrative — why this asset is positioned to outperform"
    )
    confidence: str = Field(
        description="Conviction level in the bull case: 'High', 'Medium', or 'Low'"
    )
    key_catalysts: list[str] = Field(
        description="Specific near-term or structural catalysts that support the bull case e.g. ['Margin expansion', 'AI capex tailwind', 'Undervalued vs peers']"
    )


class BearThesis(BaseModel):
    thesis: str = Field(
        description="Full bear case narrative — why this asset is at risk of underperforming"
    )
    confidence: str = Field(
        description="Conviction level in the bear case: 'High', 'Medium', or 'Low'"
    )
    key_risks: list[str] = Field(
        description="Specific risks or headwinds that support the bear case e.g. ['Valuation stretched', 'Rate sensitivity', 'Slowing revenue growth']"
    )


class ValuationData(BaseModel):
    pe_vs_sector: Optional[str] = Field(
        default=None,
        description="Qualitative comparison of the stock's P/E to its sector median e.g. 'Trading at 40% premium to sector median of 22x' — for UI display only, not passed to supervisor"
    )
    intrinsic_value_estimate: Optional[str] = Field(
        default=None,
        description="Rough intrinsic value range or fair value commentary e.g. 'Fair value estimated $140-$160 vs current $185' — for UI display only"
    )
    valuation_label: str = Field(
        description="Overall valuation judgment: 'Overvalued', 'Fairly Valued', or 'Undervalued'"
    )
    qualitative_drivers: str = Field(
        default="",
        description="2-3 sentence qualitative explanation of the valuation verdict with NO raw numbers or ratios — e.g. 'Stock trades at a significant premium relative to sector peers, pricing in growth expectations that appear optimistic given current momentum. Limited margin of safety at current levels.'"
    )
    valuation_summary: str = Field(
        description="2-3 sentence narrative with actual numbers for UI display — passed to supervisor only via qualitative_drivers"
    )


class OnChainData(BaseModel):
    market_cap_usd: Optional[float] = Field(
        default=None,
        description="Current market capitalisation in USD from CoinGecko"
    )
    volume_24h_usd: Optional[float] = Field(
        default=None,
        description="24-hour trading volume in USD — higher volume confirms price moves"
    )
    price_change_7d: Optional[float] = Field(
        default=None,
        description="7-day price change as a percentage — short-term momentum indicator"
    )
    developer_activity_score: Optional[float] = Field(
        default=None,
        description="CoinGecko developer activity score 0-100 based on GitHub commits, PRs, and contributors"
    )
    community_score: Optional[float] = Field(
        default=None,
        description="CoinGecko community score 0-100 based on social engagement across Twitter, Reddit, and Telegram"
    )
    fear_greed_score: Optional[int] = Field(
        default=None,
        description="Fear & Greed Index 0-100 from alternative.me"
    )
    fear_greed_label: Optional[str] = Field(
        default=None,
        description="Fear & Greed classification: Extreme Fear, Fear, Neutral, Greed, Extreme Greed"
    )
    btc_dominance_pct: Optional[float] = Field(
        default=None,
        description="BTC market dominance percentage from CoinGecko global"
    )
    github_momentum_pct: Optional[float] = Field(
        default=None,
        description="Developer commit momentum vs 52-week average"
    )
    network_health: str = Field(
        description="LLM-assessed network health label: 'Strong', 'Moderate', or 'Weak' — based on available CoinGecko metrics"
    )
    onchain_summary: str = Field(
        description="2-3 sentence narrative summarising the on-chain and community signals for this asset"
    )


class SupervisorReport(BaseModel):
    summary: str = Field(
        description="Executive summary synthesising all subagent outputs into a coherent investment narrative"
    )
    macro_context: str = Field(
        description="How the current macro regime specifically affects the investment case for this asset"
    )
    bull_case: str = Field(
        description="Concise restatement of the strongest bull arguments from the BullAnalyst"
    )
    bear_case: str = Field(
        description="Concise restatement of the strongest bear arguments from the BearAnalyst"
    )
    recommendation: str = Field(
        description="Final investment recommendation: 'Buy', 'Hold', or 'Sell'"
    )
    confidence: str = Field(
        description="Supervisor's overall confidence in the recommendation: 'High', 'Medium', or 'Low'"
    )
    key_metrics: list[str] = Field(
        description="The most important metrics to monitor — use qualitative labels for valuation, actual values for macro/risk. e.g. ['Earnings: Profitable ($8.26 EPS)', 'Revenue: Healthy growth (17%)', 'Valuation: Overvalued', 'Beta: 1.4', 'CPI: 3.2%', 'Regime: Risk-Off']"
    )
    analyst_agreement: str = Field(
        description="Whether the pipeline recommendation agrees with Wall Street consensus e.g. 'Agreed — both recommend Hold' or 'Disagreed — pipeline says Buy, analysts say Sell'"
    )






class FinanceState(TypedDict):
    # ── User provided ──────────────────────────────────────────────────────────
    ticker: str
    timeframe: str              # yfinance format: "1mo", "3mo", "6mo", "1y", "2y"

    # ── Pipeline derived (DataFetchAgent writes, never user-provided) ──────────
    asset_class: str            # "equity" | "crypto" — detected from yfinance quoteType

    # ── Raw data ───────────────────────────────────────────────────────────────
    raw_data: dict
    news_headlines: list[str]
    analyst_consensus: Optional[AnalystConsensus]   # promoted from raw dict

    # ── Macro (always populated regardless of asset class) ────────────────────
    macro: Optional[MacroRegimeData]

    # ── Equity subagent outputs ────────────────────────────────────────────────
    fundamentals: Optional[FundamentalsData]
    bull_thesis: Optional[BullThesis]
    bear_thesis: Optional[BearThesis]
    valuation: Optional[ValuationData]
    risk: Optional[RiskData]
    technical: Optional[dict]   # TechnicalData as dict — avoids checkpoint serializer issues

    # ── Crypto subagent outputs ────────────────────────────────────────────────
    sentiment: Optional[SentimentData]
    onchain: Optional[OnChainData]

    # ── Data provenance (injected by DataFetchAgent) ──────────────────────────
    # Carries source + age annotations for each data type.
    # e.g. {"fundamentals": "Data sourced from fmp, 3.2 hours old.", ...}
    data_provenance: Optional[dict]

    # ── Final output ───────────────────────────────────────────────────────────
    supervisor_report: Optional[SupervisorReport]