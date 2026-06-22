"""
Scans all OKX USDT spot markets, scores each by a blend of 24h volatility
and liquidity, and returns the coins that best fit a grid-trading strategy.

Grid bots profit from range-bound oscillation, not from trend strength.
The score below favors:
  - decent 24h range (volatility) -> enough swing for grid orders to fill
  - high volume -> tight spreads, orders actually get filled
  - but PENALIZES extreme one-directional moves (proxied by abs(24h change)
    being much smaller than the high/low range), since a coin that's just
    trending hard in one direction is a poor grid candidate -- it will
    walk straight through the grid and leave you holding a bag.
"""
import logging
from app.config import Config

log = logging.getLogger("scanner")


def _range_pct(ticker):
    high, low = ticker.get("high"), ticker.get("low")
    last = ticker.get("last") or ticker.get("close")
    if not high or not low or not last or low <= 0:
        return None
    return (high - low) / low * 100


def _choppiness_score(ticker, range_pct):
    """0..1, higher = more range-bound/choppy (good for grids),
    lower = more directional/trending (bad for grids)."""
    change_pct = abs(ticker.get("percentage") or 0)
    if range_pct is None or range_pct == 0:
        return 0
    # If price traveled the full range but only NET moved a little,
    # it oscillated -> good. If net change ~= full range, it just trended.
    directionality = min(change_pct / range_pct, 1.0)
    return 1 - directionality


def score_market(ticker):
    range_pct = _range_pct(ticker)
    volume_quote = ticker.get("quoteVolume") or 0
    if range_pct is None or volume_quote <= 0:
        return None

    choppiness = _choppiness_score(ticker, range_pct)
    # Normalize volatility around a target sweet spot (too low = no grid
    # profit, too high = whipsaw/risk of breakout). Sweet spot ~6-15%.
    if range_pct < 2:
        vol_score = range_pct / 2 * 0.3
    elif range_pct <= 15:
        vol_score = 0.5 + (range_pct - 2) / 13 * 0.5
    else:
        # taper off above 15% (still usable, but riskier)
        vol_score = max(0.3, 1.0 - (range_pct - 15) / 30)

    liquidity_score = min(volume_quote / 50_000_000, 1.0)  # caps at 50M/day

    score = (vol_score * 0.5) + (choppiness * 0.3) + (liquidity_score * 0.2)
    return {
        "range_pct": round(range_pct, 2),
        "change_pct": round(ticker.get("percentage") or 0, 2),
        "volume_usdt": round(volume_quote, 0),
        "choppiness": round(choppiness, 2),
        "score": round(score, 4),
    }


def discover_coins(client, quote=None, top_n=None):
    quote = quote or Config.QUOTE_CURRENCY
    top_n = top_n or Config.AUTO_DISCOVERY_TOP_N

    tickers = client.fetch_tickers()
    candidates = []

    for symbol, ticker in tickers.items():
        if not symbol.endswith(f"/{quote}"):
            continue
        base = symbol.split("/")[0]
        if base in Config.AUTO_DISCOVERY_EXCLUDE or base == quote:
            continue
        if (ticker.get("quoteVolume") or 0) < Config.AUTO_DISCOVERY_MIN_VOL_USDT:
            continue

        result = score_market(ticker)
        if not result:
            continue
        if result["range_pct"] < Config.AUTO_DISCOVERY_MIN_VOLATILITY_PCT:
            continue

        candidates.append({"symbol": symbol, "base": base, **result})

    candidates.sort(key=lambda c: c["score"], reverse=True)
    top = candidates[:top_n]
    log.info("Auto-discovery shortlisted %d/%d candidate coins", len(top), len(candidates))
    return top
