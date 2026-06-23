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
        self._check_account_mode()

    def _check_account_mode(self):
        """OKX's account-wide trading mode (acctLv) determines whether a
        'cash' (tdMode=cash) order is honoured as plain spot, or still
        gets evaluated against margin/borrowing rules. If the account is
        not in Spot mode, OKX will reject cash spot orders with a
        borrowing-related error even when tdMode is set correctly and
        the spot wallet has funds. This check surfaces that immediately
        instead of letting it look like a code bug."""
        try:
            resp = self.exchange.private_get_account_config()
            data = (resp.get("data") or [{}])[0]
            acct_lv = data.get("acctLv")
            labels = {
                "1": "Spot mode",
                "2": "Single-currency margin",
                "3": "Multi-currency margin",
                "4": "Portfolio margin",
            }
            label = labels.get(acct_lv, f"unknown ({acct_lv})")
            if acct_lv != "1":
                log.error(
                    "OKX account mode is '%s', not Spot mode. Spot 'cash' "
                    "orders will be rejected with borrowing errors until "
                    "you switch to Spot mode in OKX: Assets -> Account "
                    "Mode (do this in the SAME environment, demo or live, "
                    "that DEMO_MODE is currently pointing at).",
                    label,
                )
            else:
                log.info("OKX account mode confirmed: %s", label)
        except Exception as e:
            log.warning("Could not verify account mode (continuing anyway): %s", e)

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
