"""
Decides how much capital (in quote currency, e.g. USDT) each active coin's
grid gets, given total account equity. Higher volatility/score -> more
capital, within hard per-coin caps. The sum across all coins NEVER exceeds
Config.MAX_CAPITAL_ALLOCATION_PCT of total equity.
"""
import logging
from app.config import Config

log = logging.getLogger("allocator")


def allocate(total_equity, coin_scores):
    """
    coin_scores: dict {base_symbol: score_float (0..1+)}
    Returns: dict {base_symbol: capital_in_quote_currency}
    """
    if total_equity <= 0 or not coin_scores:
        return {}

    deployable = total_equity * Config.MAX_CAPITAL_ALLOCATION_PCT
    # guarantee reserve buffer is respected even if MAX_CAPITAL_ALLOCATION_PCT
    # was set generously
    deployable = min(deployable, total_equity * (1 - Config.RESERVE_BUFFER_PCT))

    n = len(coin_scores)
    weights = {k: max(v, 0.01) for k, v in coin_scores.items()}
    weight_sum = sum(weights.values())

    raw_alloc = {k: deployable * (w / weight_sum) for k, w in weights.items()}

    # clamp to per-coin caps
    max_per_coin = total_equity * Config.MAX_PER_COIN_PCT
    min_per_coin = total_equity * Config.MIN_PER_COIN_PCT

    clamped = {}
    overflow = 0.0
    floor_needed = 0.0
    for k, v in raw_alloc.items():
        if v > max_per_coin:
            overflow += v - max_per_coin
            clamped[k] = max_per_coin
        elif v < min_per_coin:
            floor_needed += min_per_coin - v
            clamped[k] = min_per_coin
        else:
            clamped[k] = v

    # redistribute overflow from capped coins to those still under their cap
    redistributable = [k for k in clamped if clamped[k] < max_per_coin]
    pool = overflow - floor_needed
    if pool > 0 and redistributable:
        add_each = pool / len(redistributable)
        for k in redistributable:
            clamped[k] = min(clamped[k] + add_each, max_per_coin)
    elif pool < 0:
        # not enough deployable capital to satisfy floors for every coin;
        # scale everyone down proportionally instead of exceeding budget
        scale = deployable / sum(clamped.values()) if sum(clamped.values()) > 0 else 1
        clamped = {k: v * scale for k, v in clamped.items()}

    total_used = sum(clamped.values())
    if total_used > deployable:
        scale = deployable / total_used
        clamped = {k: v * scale for k, v in clamped.items()}

    log.info(
        "Allocated %.2f of %.2f deployable equity across %d coins",
        sum(clamped.values()), deployable, n,
    )
    return clamped
