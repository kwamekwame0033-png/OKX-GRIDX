"""
Central configuration. Everything is driven by environment variables so the
same code runs locally, on Railway, or in GitHub Actions without edits.
"""
import os


def _bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    # --- OKX credentials ---
    OKX_API_KEY = os.getenv("OKX_API_KEY", "")
    OKX_SECRET = os.getenv("OKX_API_SECRET", "")
    OKX_PASSPHRASE = os.getenv("OKX_API_PASSPHRASE", "")

    # DEMO_MODE=true -> uses OKX's demo trading environment (paper money,
    # real exchange behaviour). Toggle without touching code.
    DEMO_MODE = _bool("DEMO_MODE", True)

    # --- Capital / risk controls ---
    # Never deploy more than this fraction of total account equity across
    # ALL grids combined. The bot will never exceed this no matter how many
    # coins get added or auto-discovered.
    MAX_CAPITAL_ALLOCATION_PCT = _float("MAX_CAPITAL_ALLOCATION_PCT", 0.8)

    # Minimum cash (quote currency, e.g. USDT) kept untouched as buffer.
    RESERVE_BUFFER_PCT = _float("RESERVE_BUFFER_PCT", 0.1)

    # Hard ceiling on capital % any single coin's grid can receive, so one
    # volatile coin can't swallow the whole account.
    MAX_PER_COIN_PCT = _float("MAX_PER_COIN_PCT", 0.35)

    # Minimum capital % per active coin (floor), so tiny allocations aren't
    # placed below exchange min-notional thresholds.
    MIN_PER_COIN_PCT = _float("MIN_PER_COIN_PCT", 0.03)

    # --- Grid parameters (defaults, can be overridden per-coin) ---
    DEFAULT_GRID_LEVELS = _int("DEFAULT_GRID_LEVELS", 12)
    DEFAULT_GRID_RANGE_PCT = _float("DEFAULT_GRID_RANGE_PCT", 0.10)  # +/-10% band
    GRID_MODE = os.getenv("GRID_MODE", "geometric")  # "geometric" or "arithmetic"
    TAKE_PROFIT_PER_GRID_PCT = _float("TAKE_PROFIT_PER_GRID_PCT", 0.0)  # extra edge

    # --- Coin universe ---
    QUOTE_CURRENCY = os.getenv("QUOTE_CURRENCY", "USDT")
    MANUAL_COINS = [c.strip().upper() for c in os.getenv("MANUAL_COINS", "BTC,ETH").split(",") if c.strip()]
    ENABLE_AUTO_DISCOVERY = _bool("ENABLE_AUTO_DISCOVERY", True)
    AUTO_DISCOVERY_TOP_N = _int("AUTO_DISCOVERY_TOP_N", 5)
    AUTO_DISCOVERY_MIN_VOL_USDT = _float("AUTO_DISCOVERY_MIN_VOL_USDT", 5_000_000)
    AUTO_DISCOVERY_MIN_VOLATILITY_PCT = _float("AUTO_DISCOVERY_MIN_VOLATILITY_PCT", 4.0)
    AUTO_DISCOVERY_EXCLUDE = [c.strip().upper() for c in os.getenv("AUTO_DISCOVERY_EXCLUDE", "USDC,DAI,TUSD,FDUSD").split(",")]
    MAX_TOTAL_COINS = _int("MAX_TOTAL_COINS", 10)

    # --- Loop / scheduling ---
    REBALANCE_INTERVAL_MIN = _int("REBALANCE_INTERVAL_MIN", 60)
    SCAN_INTERVAL_MIN = _int("SCAN_INTERVAL_MIN", 240)
    ORDER_SYNC_INTERVAL_SEC = _int("ORDER_SYNC_INTERVAL_SEC", 20)

    # --- Web dashboard ---
    PORT = _int("PORT", 8080)

    # --- Persistence ---
    STATE_FILE = os.getenv("STATE_FILE", "data/state.json")

    @classmethod
    def validate(cls):
        missing = [k for k in ("OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE") if not getattr(cls, k)]
        if missing:
            raise RuntimeError(
                f"Missing required OKX credentials: {', '.join(missing)}. "
                "Set them as environment variables."
            )
