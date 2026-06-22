import json
import logging
import os
import threading
import time

from app.config import Config
from app.okx_client import OKXClient
from app.scanner import discover_coins
from app.allocator import allocate
from app.grid_strategy import build_levels, adaptive_range, GridManager

log = logging.getLogger("bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


class GridBot:
    def __init__(self):
        self.client = OKXClient()
        self.managers = {}          # symbol -> GridManager
        self.coin_scores = {}       # base -> score
        self.manual_coins = set(Config.MANUAL_COINS)
        self.lock = threading.Lock()
        self.status = {
            "started_at": time.time(),
            "mode": "demo" if Config.DEMO_MODE else "live",
            "active_coins": [],
            "last_scan": None,
            "last_rebalance": None,
            "equity": 0,
            "errors": [],
        }
        os.makedirs(os.path.dirname(Config.STATE_FILE) or ".", exist_ok=True)
        self._load_state()

    # ---------------- persistence ----------------
    def _load_state(self):
        if os.path.exists(Config.STATE_FILE):
            try:
                with open(Config.STATE_FILE) as f:
                    saved = json.load(f)
                self.manual_coins |= set(saved.get("manual_coins", []))
            except Exception as e:
                log.warning("Could not load state file: %s", e)

    def _save_state(self):
        try:
            with open(Config.STATE_FILE, "w") as f:
                json.dump({"manual_coins": list(self.manual_coins)}, f)
        except Exception as e:
            log.warning("Could not save state file: %s", e)

    # ---------------- public controls (used by dashboard/API) ----------------
    def add_coin(self, base):
        base = base.upper()
        with self.lock:
            self.manual_coins.add(base)
            self._save_state()
        log.info("Manually added coin: %s", base)

    def remove_coin(self, base):
        base = base.upper()
        with self.lock:
            self.manual_coins.discard(base)
            mgr = self.managers.pop(base, None)
        if mgr:
            mgr.cancel_all()
        self._save_state()
        log.info("Removed coin: %s", base)

    def get_status(self):
        with self.lock:
            return dict(self.status)

    # ---------------- core cycles ----------------
    def scan_for_coins(self):
        scored = {}
        if Config.ENABLE_AUTO_DISCOVERY:
            try:
                candidates = discover_coins(self.client)
                for c in candidates:
                    scored[c["base"]] = c["score"]
            except Exception as e:
                log.error("Auto-discovery failed: %s", e)
                self._record_error(f"scan: {e}")

        # manual coins always included; score them too so allocator has a
        # number to work with (fallback score if not in discovery results)
        for base in self.manual_coins:
            if base not in scored:
                scored[base] = self._score_single(base)

        # enforce overall coin cap, prioritizing manual coins then best scores
        manual_first = {k: v for k, v in scored.items() if k in self.manual_coins}
        auto_rest = sorted(
            ((k, v) for k, v in scored.items() if k not in self.manual_coins),
            key=lambda kv: kv[1], reverse=True,
        )
        remaining_slots = max(0, Config.MAX_TOTAL_COINS - len(manual_first))
        final = dict(manual_first)
        final.update(dict(auto_rest[:remaining_slots]))

        self.coin_scores = final
        self.status["last_scan"] = time.time()
        self.status["active_coins"] = list(final.keys())
        log.info("Active coin set after scan: %s", final)
        return final

    def _score_single(self, base):
        try:
            ticker = self.client.fetch_ticker(f"{base}/{Config.QUOTE_CURRENCY}")
            from app.scanner import score_market
            result = score_market(ticker)
            return result["score"] if result else 0.2
        except Exception:
            return 0.2  # neutral fallback so it still gets some allocation

    def rebalance(self):
        """Recompute allocation, resize/redeploy grids, never exceeding
        account equity limits."""
        try:
            equity = self.client.get_total_equity_usdt(Config.QUOTE_CURRENCY)
        except Exception as e:
            log.error("Could not fetch equity: %s", e)
            self._record_error(f"equity fetch: {e}")
            return

        self.status["equity"] = equity
        if not self.coin_scores:
            self.scan_for_coins()

        alloc = allocate(equity, self.coin_scores)

        for base, capital in alloc.items():
            symbol = f"{base}/{Config.QUOTE_CURRENCY}"
            try:
                self._deploy_or_resize(symbol, capital)
            except Exception as e:
                log.error("Failed deploying grid for %s: %s", symbol, e)
                self._record_error(f"{symbol}: {e}")

        # tear down grids for coins no longer in the active set
        for base in list(self.managers.keys()):
            if base not in alloc:
                log.info("Decommissioning grid for %s (no longer active)", base)
                self.managers[base].cancel_all()
                del self.managers[base]

        self.status["last_rebalance"] = time.time()

    def _deploy_or_resize(self, symbol, capital):
        base = symbol.split("/")[0]
        ticker = self.client.fetch_ticker(symbol)
        price = ticker["last"]
        ohlcv = self.client.fetch_ohlcv(symbol, "1h", 24)
        range_pct = adaptive_range(ohlcv)

        existing = self.managers.get(base)
        if existing:
            # only redeploy if capital changed meaningfully (>15%) or price
            # has moved out of the current grid band -- avoids needless
            # order cancel/replace churn every cycle
            cap_changed = abs(existing.state.capital - capital) / max(existing.state.capital, 1) > 0.15
            out_of_band = price < existing.state.lower or price > existing.state.upper
            if not cap_changed and not out_of_band:
                return
            existing.cancel_all()

        state = build_levels(symbol, price, capital, range_pct=range_pct)
        mgr = GridManager(self.client, symbol, state)
        mgr.deploy()
        self.managers[base] = mgr
        log.info(
            "Grid live for %s: capital=%.2f range=[%.6f, %.6f] levels=%d",
            symbol, capital, state.lower, state.upper, len(state.levels),
        )

    def sync_orders(self):
        for mgr in list(self.managers.values()):
            try:
                mgr.sync()
            except Exception as e:
                log.warning("sync error for %s: %s", mgr.symbol, e)
                self._record_error(f"sync {mgr.symbol}: {e}")

    def _record_error(self, msg):
        self.status["errors"] = (self.status["errors"] + [{"t": time.time(), "msg": msg}])[-20:]

    # ---------------- run loop ----------------
    def run_forever(self):
        last_scan = 0
        last_rebalance = 0
        while True:
            now = time.time()
            try:
                if now - last_scan > Config.SCAN_INTERVAL_MIN * 60:
                    self.scan_for_coins()
                    last_scan = now
                if now - last_rebalance > Config.REBALANCE_INTERVAL_MIN * 60:
                    self.rebalance()
                    last_rebalance = now
                self.sync_orders()
            except Exception as e:
                log.exception("Unhandled error in main loop")
                self._record_error(f"loop: {e}")
            time.sleep(Config.ORDER_SYNC_INTERVAL_SEC)
