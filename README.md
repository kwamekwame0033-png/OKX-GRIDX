# OKX Dynamic Grid Trading Bot

Automated, multi-coin grid trading bot for OKX. Supports **live** and **demo**
(paper) trading, dynamic capital allocation, manual coin lists, and automatic
discovery of volatile coins that fit a grid strategy. Built to run on Railway.

## How it works

**1. Coin universe** — combines your `MANUAL_COINS` with auto-discovered
coins (top N by a volatility/liquidity/choppiness score across all OKX
USDT spot pairs), capped at `MAX_TOTAL_COINS`.

**2. Coin scoring (`app/scanner.py`)** — a grid bot makes money when price
oscillates inside a range, not when it trends hard one direction. Each coin
gets a score blending:
- volatility (24h high/low range) — sweet spot ~6–15%
- liquidity (24h quote volume) — ensures fills, tight spreads
- **choppiness** — penalizes coins where the net 24h move is close to the
  full range (i.e. it just trended, didn't oscillate). This is the key
  filter that keeps the bot out of strong-trending coins that would just
  walk through a grid and leave you holding losses.

**3. Dynamic capital allocation (`app/allocator.py`)** — total deployable
capital = `account_equity x MAX_CAPITAL_ALLOCATION_PCT`, always leaving
`RESERVE_BUFFER_PCT` untouched. Capital is split across active coins
weighted by score (more volatile/higher-scoring coins get more), but every
coin is clamped between `MIN_PER_COIN_PCT` and `MAX_PER_COIN_PCT` of total
equity. **The bot can never deploy more than your account balance** — this
is enforced mathematically every rebalance cycle.

**4. Adaptive grid range (`app/grid_strategy.py`)** — instead of one fixed
% width for every coin, each coin's grid range is derived from its own
realized 1h-candle volatility over the last 24h, so a calmer coin gets a
tighter grid and a wilder coin gets a wider one.

**5. Self-replenishing grid loop** — buy fills place a sell one level up;
sell fills place a buy one level down. This is the standard grid mechanic
and is what actually harvests profit from oscillation.

**6. Rebalancing** — every `REBALANCE_INTERVAL_MIN`, equity and scores are
re-evaluated and grids are resized/redeployed only if capital changed
meaningfully or price moved outside the current band (avoids needless
churn/fees).

## Demo vs Live

Set `DEMO_MODE=true` to use OKX's demo trading environment (paper money on
real exchange behavior). You need separate API keys generated from OKX's
demo trading panel — they're not the same as your live keys. Flip to
`DEMO_MODE=false` with live keys when you're ready.

## Setup

```bash
git clone <your-repo>
cd okx-grid-bot
cp .env.example .env   # fill in your OKX keys
pip install -r requirements.txt
python main.py
```

Dashboard: `http://localhost:8080/status`

## Deploying on Railway

1. Push this repo to GitHub.
2. New Railway project -> "Deploy from GitHub repo".
3. In Railway -> Variables, paste in everything from `.env.example` with
   your real values (never commit `.env`).
4. Railway auto-detects the `Procfile` and runs `python main.py`.
5. Add a volume mount (or a Railway Postgres add-on, see ideas below) if
   you want the manual coin list to persist across redeploys — otherwise
   it resets to the `MANUAL_COINS` env var on every deploy.

## API / Dashboard endpoints

- `GET /status` — equity, active coins, scores, live grid state, recent errors
- `GET /coins` — manual + active coin lists
- `POST /coins {"coin": "SOL"}` — add a coin manually
- `DELETE /coins/SOL` — remove a coin and cancel its grid

## Tuning knobs (env vars, see `.env.example`)

| Variable | Effect |
|---|---|
| `MAX_CAPITAL_ALLOCATION_PCT` | Ceiling on total % of equity ever deployed |
| `MAX_PER_COIN_PCT` / `MIN_PER_COIN_PCT` | Per-coin allocation bounds |
| `DEFAULT_GRID_LEVELS` | Number of grid rungs |
| `GRID_MODE` | `geometric` (% spaced) or `arithmetic` (equal $ spaced) |
| `AUTO_DISCOVERY_MIN_VOLATILITY_PCT` | Floor for auto-discovered coins |
| `REBALANCE_INTERVAL_MIN` | How often allocation/grids are recalculated |

## Suggestions & ideas to extend this further

- **Stop-loss / circuit breaker**: add a max-drawdown check per coin (e.g.
  if price closes 2x outside the grid band, cancel and exit instead of
  silently waiting) — protects against a coin breaking trend despite the
  choppiness filter.
- **Per-grid P&L tracking**: log every buy/sell pair with realized profit,
  store in SQLite, and surface a cumulative P&L chart on the dashboard.
- **Telegram/Discord alerts**: push a message on grid deploy, fill,
  errors, or large drawdown — faster than polling `/status`.
- **Funding-rate aware variant**: if you add OKX perpetuals later, a grid +
  funding-rate combo can add yield, but it's a materially different risk
  profile (leverage, liquidation risk) — keep it as a separate strategy.
- **Backtesting harness**: replay OHLCV history through the grid level
  logic to estimate expected profit before going live on a new coin.
- **Volatility regime switch**: widen/narrow `DEFAULT_GRID_RANGE_PCT`
  bot-wide based on overall market volatility (e.g. BTC's realized vol),
  so the whole portfolio adapts to risk-on/risk-off regimes.
- **Database instead of a single JSON state file** once running many
  coins — Railway's filesystem is ephemeral, so a Postgres add-on (Railway
  offers one natively) is safer than a volume mount long-term.
- **Slippage/fee-aware spacing**: ensure grid spacing always clears OKX
  maker/taker fees + expected slippage, or tight grids bleed fees faster
  than they earn spread profit. Tune `TAKE_PROFIT_PER_GRID_PCT` per coin.

## Risk notes

- Start in `DEMO_MODE=true` and watch at least a few rebalance cycles
  before going live.
- Grid trading does not protect against a coin breaking out of range and
  trending hard — the choppiness filter and adaptive range reduce but do
  not eliminate this risk.
- This code is provided as-is; review and test thoroughly before risking
  real capital. Not financial advice.
