"""
src/nodes/technical_analyst.py — TechnicalAnalyst agent

Computes RSI, MACD, MA crossover, ATR, and volume trend from price history
already in state. Converts all signals to qualitative labels before the LLM
sees them — same pattern as bull/bear analysts.

No new API calls. No new data sources. Pure pandas math on history_df.
"""

import logging
import pandas as pd
import numpy as np
from langchain_core.messages import HumanMessage
from src.states.financestate import FinanceState
from src.exceptions import LLMStructuredOutputError
from pydantic import BaseModel, Field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Output model ──────────────────────────────────────────────────────────────

class TechnicalData(BaseModel):
    signal: str = Field(
        description="Overall technical signal: 'Bullish', 'Bearish', or 'Neutral'"
    )
    trend: str = Field(
        description="Price trend direction: 'Uptrend', 'Downtrend', or 'Sideways'"
    )
    momentum: str = Field(
        description="Momentum strength: 'Strong', 'Weak', or 'Diverging'"
    )
    key_levels: list[str] = Field(
        description="3-5 specific technical observations e.g. ['RSI overbought at 78', 'Below 200-day MA', 'MACD bearish crossover']"
    )
    summary: str = Field(
        description="2-3 sentence technical read synthesising all indicators into a clear directional view"
    )
    atr_pct: Optional[float] = Field(
        default=None,
        description="ATR as % of current price — used for position sizing"
    )


# ── Indicator computation ─────────────────────────────────────────────────────

def _compute_indicators(df: pd.DataFrame) -> dict:
    """
    Compute technical indicators from OHLCV dataframe.
    Returns plain dict of labeled signals — no raw floats passed to LLM.
    """
    result = {}

    try:
        close = df["Close"].dropna()
        volume = df["Volume"].dropna() if "Volume" in df.columns else None
        high = df["High"].dropna() if "High" in df.columns else close
        low = df["Low"].dropna() if "Low" in df.columns else close

        if len(close) < 20:
            return {"error": "Insufficient price history (need 20+ days)"}

        current_price = float(close.iloc[-1])

        # ── RSI (14-period) ───────────────────────────────────────────────────
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None

        if rsi_val is not None:
            if rsi_val > 70:
                result["rsi"] = f"RSI overbought at {rsi_val:.0f} — momentum stretched, pullback risk"
            elif rsi_val < 30:
                result["rsi"] = f"RSI oversold at {rsi_val:.0f} — potential bounce zone"
            elif rsi_val > 55:
                result["rsi"] = f"RSI bullish territory at {rsi_val:.0f}"
            elif rsi_val < 45:
                result["rsi"] = f"RSI bearish territory at {rsi_val:.0f}"
            else:
                result["rsi"] = f"RSI neutral at {rsi_val:.0f}"
            result["rsi_val"] = rsi_val

        # ── MACD (12/26/9) ────────────────────────────────────────────────────
        if len(close) >= 26:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line

            macd_val = float(macd_line.iloc[-1])
            signal_val = float(signal_line.iloc[-1])
            hist_val = float(histogram.iloc[-1])
            prev_hist = float(histogram.iloc[-2]) if len(histogram) > 1 else hist_val

            if macd_val > signal_val and hist_val > 0:
                if hist_val > prev_hist:
                    result["macd"] = "MACD bullish crossover with accelerating momentum"
                else:
                    result["macd"] = "MACD above signal line — bullish but momentum slowing"
            elif macd_val < signal_val and hist_val < 0:
                if hist_val < prev_hist:
                    result["macd"] = "MACD bearish crossover with accelerating downside momentum"
                else:
                    result["macd"] = "MACD below signal line — bearish but momentum stabilising"
            else:
                result["macd"] = "MACD near crossover — directional signal unclear"

        # ── Moving Averages (50/200) ───────────────────────────────────────────
        if len(close) >= 50:
            ma50 = close.rolling(50).mean()
            ma50_val = float(ma50.iloc[-1])
            above_50 = current_price > ma50_val
            pct_vs_50 = ((current_price - ma50_val) / ma50_val) * 100

            if len(close) >= 200:
                ma200 = close.rolling(200).mean()
                ma200_val = float(ma200.iloc[-1])
                above_200 = current_price > ma200_val
                pct_vs_200 = ((current_price - ma200_val) / ma200_val) * 100

                # Golden/death cross
                prev_ma50 = float(ma50.iloc[-2]) if len(ma50.dropna()) > 1 else ma50_val
                prev_ma200 = float(ma200.iloc[-2]) if len(ma200.dropna()) > 1 else ma200_val

                if ma50_val > ma200_val and prev_ma50 <= prev_ma200:
                    result["ma"] = f"Golden cross — 50MA just crossed above 200MA (bullish trend change)"
                elif ma50_val < ma200_val and prev_ma50 >= prev_ma200:
                    result["ma"] = f"Death cross — 50MA just crossed below 200MA (bearish trend change)"
                elif above_200 and above_50:
                    result["ma"] = f"Price above both 50MA and 200MA — established uptrend ({pct_vs_200:+.1f}% vs 200MA)"
                elif not above_200 and not above_50:
                    result["ma"] = f"Price below both 50MA and 200MA — established downtrend ({pct_vs_200:+.1f}% vs 200MA)"
                else:
                    result["ma"] = f"Price between 50MA and 200MA — transitional zone ({pct_vs_50:+.1f}% vs 50MA)"

                result["above_200ma"] = above_200
            else:
                result["ma"] = f"Price {'above' if above_50 else 'below'} 50MA ({pct_vs_50:+.1f}%) — 200MA requires more history"
                result["above_200ma"] = None

        # ── ATR (14-period) ───────────────────────────────────────────────────
        if len(close) >= 14:
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(14).mean()
            atr_val = float(atr.iloc[-1])
            atr_pct = (atr_val / current_price) * 100
            result["atr_pct"] = round(atr_pct, 2)

            if atr_pct > 3:
                result["atr"] = f"High volatility — ATR {atr_pct:.1f}% of price (elevated risk per trade)"
            elif atr_pct > 1.5:
                result["atr"] = f"Moderate volatility — ATR {atr_pct:.1f}% of price"
            else:
                result["atr"] = f"Low volatility — ATR {atr_pct:.1f}% of price (compressed range)"

        # ── Volume trend ──────────────────────────────────────────────────────
        if volume is not None and len(volume) >= 20:
            avg_vol_20 = float(volume.rolling(20).mean().iloc[-1])
            recent_vol = float(volume.iloc[-1])
            vol_ratio = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

            price_up = float(close.iloc[-1]) > float(close.iloc[-2])
            if vol_ratio > 1.5 and price_up:
                result["volume"] = f"High volume ({vol_ratio:.1f}x avg) confirming price advance — conviction buying"
            elif vol_ratio > 1.5 and not price_up:
                result["volume"] = f"High volume ({vol_ratio:.1f}x avg) on down move — distribution signal"
            elif vol_ratio < 0.7:
                result["volume"] = f"Low volume ({vol_ratio:.1f}x avg) — weak conviction in current move"
            else:
                result["volume"] = f"Average volume ({vol_ratio:.1f}x avg) — no exceptional conviction"

    except Exception as e:
        logger.warning(f"Indicator computation partial failure: {e}")
        result["error"] = str(e)

    return result


def _derive_signal(indicators: dict) -> tuple[str, str, str]:
    """Derive overall signal, trend, momentum from computed indicators."""
    bullish_points = 0
    bearish_points = 0

    rsi_val = indicators.get("rsi_val")
    if rsi_val is not None:
        if rsi_val > 55: bullish_points += 1
        elif rsi_val < 45: bearish_points += 1
        if rsi_val > 70: bearish_points += 1  # overbought is a risk
        if rsi_val < 30: bullish_points += 1  # oversold is an opportunity

    macd = indicators.get("macd", "")
    if "bullish" in macd.lower(): bullish_points += 2
    elif "bearish" in macd.lower(): bearish_points += 2

    above_200 = indicators.get("above_200ma")
    if above_200 is True: bullish_points += 2
    elif above_200 is False: bearish_points += 2

    volume = indicators.get("volume", "")
    if "conviction buying" in volume: bullish_points += 1
    elif "distribution" in volume: bearish_points += 1

    # Signal
    if bullish_points > bearish_points + 1:
        signal = "Bullish"
    elif bearish_points > bullish_points + 1:
        signal = "Bearish"
    else:
        signal = "Neutral"

    # Trend from MA
    ma = indicators.get("ma", "")
    if "above both" in ma or "golden cross" in ma:
        trend = "Uptrend"
    elif "below both" in ma or "death cross" in ma:
        trend = "Downtrend"
    else:
        trend = "Sideways"

    # Momentum from MACD + RSI
    if "accelerating" in macd and signal == "Bullish":
        momentum = "Strong"
    elif "accelerating" in macd and signal == "Bearish":
        momentum = "Strong"
    elif "slowing" in macd or "stabilising" in macd:
        momentum = "Weak"
    elif rsi_val and ((rsi_val > 70 and "bearish" in macd.lower()) or (rsi_val < 30 and "bullish" in macd.lower())):
        momentum = "Diverging"
    else:
        momentum = "Weak"

    return signal, trend, momentum


# ── Prompt ────────────────────────────────────────────────────────────────────

TECHNICAL_PROMPT = """You are the Technical Analyst at an elite investment research firm.
Your job is to synthesise the computed technical indicators below into a clear directional view.
You are reading price action, not making fundamental judgments — stay in your lane.

CRITICAL: You have been given pre-computed qualitative indicators. Do NOT invent additional metrics,
price levels, or indicator values not present below. Your job is synthesis, not computation.

--- TICKER ---
{ticker}

--- COMPUTED INDICATORS ---
{indicators_text}

--- DERIVED SIGNALS ---
Overall Signal: {signal}
Trend: {trend}
Momentum: {momentum}

Your output:
- signal: '{signal}' (use exactly as provided)
- trend: '{trend}' (use exactly as provided)
- momentum: '{momentum}' (use exactly as provided)
- key_levels: List 3-5 of the most important observations from the indicators above. Be specific — quote the indicator values given.
- summary: 2-3 sentences synthesising the technical picture. What does price action say about near-term direction? Where is risk?
"""


# ── Agent ─────────────────────────────────────────────────────────────────────

class TechnicalAnalyst:

    def __init__(self, llm):
        self.llm = llm.with_structured_output(TechnicalData)

    def analyze(self, state: FinanceState, **kwargs) -> dict:
        ticker = state["ticker"]
        asset_class = state.get("asset_class", "equity")
        logger.info(f"TechnicalAnalyst starting for {ticker}...")

        # Skip for crypto — use OnchainAnalyst instead
        if asset_class == "crypto":
            logger.info(f"TechnicalAnalyst skipping {ticker} — crypto uses OnchainAnalyst")
            return {"technical": None}

        raw_data = state.get("raw_data") or {}
        history_raw = raw_data.get("history")

        if not history_raw:
            logger.warning(f"TechnicalAnalyst: no price history for {ticker}")
            return {"technical": None}

        try:
            df = pd.DataFrame(history_raw)
            if df.empty or "Close" not in df.columns:
                logger.warning(f"TechnicalAnalyst: empty or malformed history for {ticker}")
                return {"technical": None}
        except Exception as e:
            logger.error(f"TechnicalAnalyst: failed to build DataFrame for {ticker}: {e}")
            return {"technical": None}

        indicators = _compute_indicators(df)
        signal, trend, momentum = _derive_signal(indicators)

        # Build indicators text for prompt
        indicator_lines = []
        for key in ["rsi", "macd", "ma", "atr", "volume"]:
            if key in indicators:
                indicator_lines.append(f"• {indicators[key]}")
        if not indicator_lines:
            indicator_lines = ["• Insufficient data for full indicator suite"]
        indicators_text = "\n".join(indicator_lines)

        prompt = TECHNICAL_PROMPT.format(
            ticker=ticker,
            indicators_text=indicators_text,
            signal=signal,
            trend=trend,
            momentum=momentum,
        )

        try:
            result: TechnicalData = self.llm.invoke([HumanMessage(content=prompt)])
            # Attach ATR for position sizing use downstream
            result_dict = result.model_dump()
            result_dict["atr_pct"] = indicators.get("atr_pct")
            logger.info(f"TechnicalAnalyst complete for {ticker} — signal={result.signal} trend={result.trend}")
            return {"technical": result_dict}
        except Exception as e:
            logger.error(f"TechnicalAnalyst LLM call failed for {ticker}: {e}", exc_info=True)
            raise LLMStructuredOutputError(f"TechnicalAnalyst failed: {e}")