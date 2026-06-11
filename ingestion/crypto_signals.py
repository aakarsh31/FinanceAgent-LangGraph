"""
ingestion/crypto_signals.py — Crypto market structure signals

Fetches orthogonal signals that complement price analysis:
- Fear & Greed Index (market-wide, once per run)
- BTC Dominance (market-wide, once per run)
- Price momentum 7d/30d/ATH (per ticker, CoinGecko)
- Developer activity (per ticker, GitHub + CoinGecko fallback)

Design decisions:
- Market-wide signals fetched once, shared across all tickers
- Per-ticker signals fetched individually — CoinGecko one call per ticker
- GitHub used for momentum vs baseline, CoinGecko as fallback for forks
- All failures are non-fatal — missing signals return None, never crash pipeline
- to_prompt_context() formats everything for direct injection into CryptoAgent prompt
"""

import logging
import requests
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── GitHub repo map ───────────────────────────────────────────────────────────
# None = no reliable GitHub repo, fall back to CoinGecko commits only

GITHUB_REPOS: dict[str, Optional[str]] = {
    "BTC-USD":   "bitcoin/bitcoin",
    "ETH-USD":   "ethereum/go-ethereum",
    "SOL-USD":   None,                        # anza-xyz/agave is a fork — stats unreliable
    "BNB-USD":   "bnb-chain/bsc",
    "AVAX-USD":  "ava-labs/avalanchego",
    "LINK-USD":  "smartcontractkit/chainlink",
    "DOGE-USD":  None,                        # no meaningful dev repo
    "UNI-USD":   "Uniswap/v3-core",
    "ATOM-USD":  "cosmos/cosmos-sdk",
    "DOT-USD":   "paritytech/polkadot",
    "XRP-USD":   None,                        # XRPL is closed-source
    "ADA-USD":   "input-output-hk/cardano-node",
    "MATIC-USD": "maticnetwork/bor",
    "TRX-USD":   None,
}

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class DeveloperSignal:
    commits_4w: Optional[int]
    code_additions_4w: Optional[int]
    github_momentum_pct: Optional[float]  # None if GitHub unavailable
    source: str                           # "github+coingecko" or "coingecko"


@dataclass
class CryptoSignals:
    ticker: str

    # Market sentiment (market-wide)
    fear_greed_value: Optional[int]
    fear_greed_label: Optional[str]       # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"

    # Market structure (market-wide)
    btc_dominance_pct: Optional[float]

    # Price momentum (per ticker)
    price_change_7d: Optional[float]
    price_change_30d: Optional[float]
    ath_change_pct: Optional[float]

    # Developer activity (per ticker)
    developer: Optional[DeveloperSignal]

    def to_prompt_context(self) -> str:
        """
        Returns formatted string for direct injection into CryptoAgent prompt.
        Only includes fields that have data — no "None" strings in the output.
        """
        lines = []

        if self.fear_greed_value is not None:
            lines.append(
                f"Market Sentiment: {self.fear_greed_value}/100 ({self.fear_greed_label})"
            )

        if self.btc_dominance_pct is not None:
            lines.append(f"BTC Dominance: {self.btc_dominance_pct:.1f}%")

        if self.price_change_7d is not None:
            parts = [f"7d={self.price_change_7d:+.1f}%"]
            if self.price_change_30d is not None:
                parts.append(f"30d={self.price_change_30d:+.1f}%")
            if self.ath_change_pct is not None:
                parts.append(f"vs ATH={self.ath_change_pct:+.1f}%")
            lines.append(f"Price Momentum: {'  '.join(parts)}")

        if self.developer:
            dev = self.developer
            if dev.github_momentum_pct is not None:
                lines.append(
                    f"Developer Activity: {dev.commits_4w} commits/4w "
                    f"({dev.github_momentum_pct:.0f}% of 52-week avg) "
                    f"via {dev.source}"
                )
            elif dev.commits_4w is not None:
                lines.append(
                    f"Developer Activity: {dev.commits_4w} commits/4w "
                    f"via {dev.source}"
                )

        return "\n".join(lines) if lines else "No market structure signals available."


# ── Fetch functions ───────────────────────────────────────────────────────────

def fetch_fear_greed() -> tuple[Optional[int], Optional[str]]:
    """
    Market-wide signal. Fetch once per pipeline run.
    Returns (value, label) or (None, None) on failure.
    """
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10
        )
        resp.raise_for_status()
        entry = resp.json()["data"][0]
        return int(entry["value"]), entry["value_classification"]
    except Exception as e:
        logger.warning(f"crypto_signals: fear_greed fetch failed: {e}")
        return None, None


def fetch_btc_dominance() -> Optional[float]:
    """
    Market-wide signal. Fetch once per pipeline run.
    Returns BTC dominance % e.g. 56.05 or None on failure.
    """
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return round(float(data["market_cap_percentage"]["btc"]), 2)
    except Exception as e:
        logger.warning(f"crypto_signals: btc_dominance fetch failed: {e}")
        return None


def fetch_coingecko_signals(coin_id: str) -> dict:
    """
    Per-ticker CoinGecko call.
    Returns price momentum + developer data in one request.
    """
    try:
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={
                "localization":   "false",
                "tickers":        "false",
                "market_data":    "true",
                "community_data": "false",
                "developer_data": "true",
            },
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        market = data.get("market_data", {})
        dev    = data.get("developer_data", {})

        return {
            "price_change_7d":  market.get("price_change_percentage_7d"),
            "price_change_30d": market.get("price_change_percentage_30d"),
            "ath_change_pct":   market.get("ath_change_percentage", {}).get("usd"),
            "commits_4w":       dev.get("commit_count_4_weeks"),
            "code_additions":   dev.get("code_additions_deletions_4_weeks", {}).get("additions"),
        }
    except Exception as e:
        logger.warning(f"crypto_signals: coingecko fetch failed for {coin_id}: {e}")
        return {}


def fetch_github_momentum(repo: str) -> Optional[float]:
    """
    Per-ticker GitHub call.
    Returns commit momentum as % of 52-week average.
    e.g. 119.0 = 19% above average, 79.0 = 21% below average.
    Returns None if unavailable or repo is a fork.
    """
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/stats/commit_activity",
            timeout=15,
            headers={"Accept": "application/vnd.github.v3+json"}
        )
        if resp.status_code == 202:
            logger.info(f"crypto_signals: GitHub computing stats for {repo} — will use CoinGecko fallback")
            return None
        if resp.status_code != 200:
            return None

        data = resp.json()
        if not data:
            return None

        recent_4w  = sum(w["total"] for w in data[-4:])
        avg_weekly = sum(w["total"] for w in data) / len(data)

        if avg_weekly == 0:
            return None

        return round((recent_4w / 4 / avg_weekly) * 100, 1)

    except Exception as e:
        logger.warning(f"crypto_signals: github fetch failed for {repo}: {e}")
        return None


# ── Main assembly function ────────────────────────────────────────────────────

# CoinGecko coin ID map — same as existing data_fetch.py COINGECKO_IDS
COINGECKO_IDS: dict[str, str] = {
    "BTC-USD":   "bitcoin",
    "ETH-USD":   "ethereum",
    "SOL-USD":   "solana",
    "BNB-USD":   "binancecoin",
    "XRP-USD":   "ripple",
    "ADA-USD":   "cardano",
    "DOGE-USD":  "dogecoin",
    "AVAX-USD":  "avalanche-2",
    "MATIC-USD": "matic-network",
    "DOT-USD":   "polkadot",
    "LINK-USD":  "chainlink",
    "LTC-USD":   "litecoin",
    "UNI-USD":   "uniswap",
    "ATOM-USD":  "cosmos",
    "TRX-USD":   "tron",
}


def build_crypto_signals(
    ticker: str,
    fear_greed: tuple[Optional[int], Optional[str]],
    btc_dominance: Optional[float],
) -> CryptoSignals:
    """
    Build complete CryptoSignals for a single ticker.

    Args:
        ticker: e.g. "BTC-USD"
        fear_greed: result of fetch_fear_greed() — passed in, not re-fetched
        btc_dominance: result of fetch_btc_dominance() — passed in, not re-fetched

    Returns CryptoSignals with all available data populated.
    """
    coin_id   = COINGECKO_IDS.get(ticker.upper())
    gh_repo   = GITHUB_REPOS.get(ticker.upper())

    # Per-ticker CoinGecko fetch
    cg_data = fetch_coingecko_signals(coin_id) if coin_id else {}

    # Developer signal — GitHub momentum + CoinGecko commits
    commits_4w   = cg_data.get("commits_4w")
    code_adds    = cg_data.get("code_additions")
    gh_momentum  = fetch_github_momentum(gh_repo) if gh_repo else None
    source       = "github+coingecko" if gh_momentum is not None else "coingecko"

    developer = DeveloperSignal(
        commits_4w=commits_4w,
        code_additions_4w=code_adds,
        github_momentum_pct=gh_momentum,
        source=source,
    ) if (commits_4w is not None or gh_momentum is not None) else None

    return CryptoSignals(
        ticker=ticker,
        fear_greed_value=fear_greed[0],
        fear_greed_label=fear_greed[1],
        btc_dominance_pct=btc_dominance,
        price_change_7d=cg_data.get("price_change_7d"),
        price_change_30d=cg_data.get("price_change_30d"),
        ath_change_pct=cg_data.get("ath_change_pct"),
        developer=developer,
    )
