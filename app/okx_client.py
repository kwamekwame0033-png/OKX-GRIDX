"""
Thin wrapper around ccxt's OKX implementation.

Demo trading: OKX's "demo trading" (paper trading) environment is selected
by sending the header `x-simulated-trading: 1` on every request, using API
keys generated specifically in OKX's demo trading panel (these are different
from your live keys). This wrapper toggles that automatically from
Config.DEMO_MODE.
"""
import ccxt
import logging

from app.config import Config

log = logging.getLogger("okx_client")


class OKXClient:
    def __init__(self):
        Config.validate()
        self.demo = Config.DEMO_MODE
        self.exchange = ccxt.okx({
            "apiKey": Config.OKX_API_KEY,
            "secret": Config.OKX_SECRET,
            "password": Config.OKX_PASSPHRASE,
            "enableRateLimit": True,
        })
        if self.demo:
            # ccxt's built-in sandbox switch sets the simulated-trading header
            # and points at the same REST host OKX uses for demo trading.
            self.exchange.set_sandbox_mode(True)
            log.warning("OKX client running in DEMO (paper trading) mode.")
        else:
            log.warning("OKX client running in LIVE mode. Real funds at risk.")

        self.exchange.load_markets()

    # ---------- account ----------
    def get_balance(self, currency=None):
        bal = self.exchange.fetch_balance()
        if currency:
            return bal.get(currency, {}).get("free", 0.0) or 0.0
        return bal

    def get_total_equity_usdt(self, quote="USDT"):
        """Free + used balance in the quote currency, plus mark-to-market
        value of any coin positions, expressed in `quote`."""
        bal = self.exchange.fetch_balance()
        total = bal.get(quote, {}).get("total", 0.0) or 0.0
        for asset, info in bal.get("total", {}).items() if isinstance(bal.get("total"), dict) else []:
            if asset == quote or not info:
                continue
            try:
                ticker = self.exchange.fetch_ticker(f"{asset}/{quote}")
                total += info * (ticker["last"] or 0)
            except Exception:
                continue
        return total

    # ---------- market data ----------
    def fetch_ticker(self, symbol):
        return self.exchange.fetch_ticker(symbol)

    def fetch_tickers(self):
        return self.exchange.fetch_tickers()

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=24):
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def all_spot_markets(self, quote="USDT"):
        markets = self.exchange.load_markets()
        return [
            m for m in markets.values()
            if m.get("spot") and m.get("quote") == quote and m.get("active", True)
        ]

    # ---------- trading ----------
    def create_limit_order(self, symbol, side, amount, price):
        # tdMode 'cash' forces plain spot trading (spend what you have).
        # Without this, OKX can default to a margin trade mode, which
        # tries to BORROW instead of spending your spot USDT -- that's
        # what causes "available margin too low for borrowing" errors
        # even when your spot wallet has plenty of balance.
        return self.exchange.create_order(
            symbol, "limit", side, amount, price,
            params={"tdMode": "cash"},
        )

    def cancel_order(self, order_id, symbol):
        return self.exchange.cancel_order(order_id, symbol)

    def fetch_open_orders(self, symbol=None):
        return self.exchange.fetch_open_orders(symbol)

    def fetch_order(self, order_id, symbol):
        return self.exchange.fetch_order(order_id, symbol)

    def min_notional(self, symbol):
        market = self.exchange.market(symbol)
        limits = market.get("limits", {})
        cost_min = (limits.get("cost") or {}).get("min")
        amount_min = (limits.get("amount") or {}).get("min")
        return cost_min, amount_min
