"""
Per-coin grid strategy.

Grid range and spacing adapt to the coin's own recent volatility (ATR-like
24h high/low range) rather than a fixed % for every coin -- a stablecoin-ish
mover and a wild altcoin should not use the same grid width.

Order book of the grid:
  - price range = current price +/- adaptive_range_pct
  - N levels, geometric (% spaced) or arithmetic (equal $ spaced)
  - a limit BUY sits at every level below current price
  - a limit SELL sits at every level above current price (only if/once we
    hold inventory from a filled buy at that level -- see GridState)
  - on a buy fill -> place a sell one level up
  - on a sell fill -> place a buy one level down
  - this is the standard self-replenishing grid loop
"""
import logging
import time
from dataclasses import dataclass, field

from app.config import Config

log = logging.getLogger("grid")


@dataclass
class GridLevel:
    price: float
    side: str  # "buy" placeholder slot, becomes a live order
    order_id: str = None
    status: str = "pending"  # pending/open/filled


@dataclass
class GridState:
    symbol: str
    capital: float
    lower: float
    upper: float
    levels: list = field(default_factory=list)
    base_qty_per_level: float = 0.0
    last_rebalance: float = field(default_factory=time.time)


def adaptive_range(ohlcv_1h, fallback_pct=None):
    """Derive a +/- range % from recent 1h candle highs/lows (last 24h)."""
    fallback_pct = fallback_pct or Config.DEFAULT_GRID_RANGE_PCT
    if not ohlcv_1h or len(ohlcv_1h) < 6:
        return fallback_pct
    highs = [c[2] for c in ohlcv_1h]
    lows = [c[3] for c in ohlcv_1h]
    closes = [c[4] for c in ohlcv_1h]
    avg_close = sum(closes) / len(closes)
    if avg_close <= 0:
        return fallback_pct
    swing_pct = (max(highs) - min(lows)) / avg_close
    # use ~70% of the realized swing as the grid half-range, bounded sanely
    half_range = swing_pct * 0.7
    return max(0.03, min(half_range, 0.35))


def build_levels(symbol, current_price, capital, levels_n=None, range_pct=None, mode=None):
    levels_n = levels_n or Config.DEFAULT_GRID_LEVELS
    range_pct = range_pct if range_pct is not None else Config.DEFAULT_GRID_RANGE_PCT
    mode = mode or Config.GRID_MODE

    lower = current_price * (1 - range_pct)
    upper = current_price * (1 + range_pct)

    prices = []
    if mode == "geometric":
        ratio = (upper / lower) ** (1 / (levels_n - 1))
        prices = [lower * (ratio ** i) for i in range(levels_n)]
    else:  # arithmetic
        step = (upper - lower) / (levels_n - 1)
        prices = [lower + step * i for i in range(levels_n)]

    capital_per_level = capital / levels_n
    levels = []
    for p in prices:
        side = "buy" if p < current_price else "sell"
        levels.append(GridLevel(price=round(p, 8), side=side))

    state = GridState(
        symbol=symbol,
        capital=capital,
        lower=lower,
        upper=upper,
        levels=levels,
        base_qty_per_level=capital_per_level,
    )
    return state


class GridManager:
    """Places and maintains the live order ladder for one symbol."""

    def __init__(self, client, symbol, state: GridState):
        self.client = client
        self.symbol = symbol
        self.state = state

    def deploy(self):
        """Place initial buy orders below price, sell orders above (sell
        side only if we already hold inventory -- otherwise skipped until
        a buy fills, which is standard grid behaviour for a fresh deploy)."""
        placed = 0
        cost_min, amount_min = self.client.min_notional(self.symbol)
        for level in self.state.levels:
            qty = self.state.base_qty_per_level / level.price
            if cost_min and self.state.base_qty_per_level < cost_min:
                continue
            if amount_min and qty < amount_min:
                continue
            if level.side != "buy":
                continue  # initial deploy only seeds buy side; sells are
                          # created reactively once a buy fills (see on_fill)
            try:
                order = self.client.create_limit_order(self.symbol, "buy", qty, level.price)
                level.order_id = order["id"]
                level.status = "open"
                placed += 1
            except Exception as e:
                log.warning("Failed to place grid order %s @ %s: %s", self.symbol, level.price, e)
        log.info("Deployed %d buy orders for %s", placed, self.symbol)
        return placed

    def sync(self):
        """Check open orders; on fill, place the mirrored order one grid
        step in the opposite direction (the core grid recycling logic)."""
        try:
            open_orders = {o["id"] for o in self.client.fetch_open_orders(self.symbol)}
        except Exception as e:
            log.warning("sync(): could not fetch open orders for %s: %s", self.symbol, e)
            return

        for i, level in enumerate(self.state.levels):
            if level.status != "open" or not level.order_id:
                continue
            if level.order_id in open_orders:
                continue  # still resting

            # order_id disappeared from open orders -> filled (or cancelled)
            try:
                order = self.client.fetch_order(level.order_id, self.symbol)
            except Exception:
                continue
            if order.get("status") != "closed":
                continue

            level.status = "filled"
            self._on_fill(i, level, order)

    def _on_fill(self, idx, level, order):
        side_filled = order.get("side")
        qty = order.get("filled") or order.get("amount")
        edge = 1 + Config.TAKE_PROFIT_PER_GRID_PCT

        if side_filled == "buy" and idx + 1 < len(self.state.levels):
            target = self.state.levels[idx + 1]
            try:
                sell_order = self.client.create_limit_order(
                    self.symbol, "sell", qty, round(target.price * edge, 8)
                )
                target.order_id = sell_order["id"]
                target.status = "open"
                target.side = "sell"
                log.info("%s: buy filled @%s -> placed sell @%s", self.symbol, level.price, target.price)
            except Exception as e:
                log.warning("Failed to place mirrored sell for %s: %s", self.symbol, e)

        elif side_filled == "sell" and idx - 1 >= 0:
            target = self.state.levels[idx - 1]
            try:
                buy_order = self.client.create_limit_order(
                    self.symbol, "buy", qty, round(target.price / edge, 8)
                )
                target.order_id = buy_order["id"]
                target.status = "open"
                target.side = "buy"
                log.info("%s: sell filled @%s -> placed buy @%s", self.symbol, level.price, target.price)
            except Exception as e:
                log.warning("Failed to place mirrored buy for %s: %s", self.symbol, e)

    def cancel_all(self):
        for level in self.state.levels:
            if level.status == "open" and level.order_id:
                try:
                    self.client.cancel_order(level.order_id, self.symbol)
                except Exception:
                    pass
                level.status = "cancelled"
