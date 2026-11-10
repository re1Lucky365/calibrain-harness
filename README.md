# Classifier Trading Terminal

A Bloomberg-style trading terminal, forward-testing harness and bot for **Kairos** (`DMIND/Kairos` on Minara Cloud) on
**Bybit USDT perpetuals**, long and short, with exchange-side stop loss and take profit on
the **Bybit testnet**. Every prediction Kairos makes is scored against what the market did,
so you get both a trading bot and an honest evaluation of the model.

```
Bybit mainnet public data (WS + REST)        news RSS · Fear&Greed · macro (Yahoo)
  klines 1m/5m/15m/1h/4h, tickers, funding,          │
  OI, long/short accounts, liquidations,             │
  taker trades, order book (50)                      │
        │                                            │
        └──► features.py: compact, unit-labelled state (~1.1k tokens)
                   │
                   ▼
   Kairos /v1/systemone: 8 labellable questions per closed 5m bar per symbol
   (long bracket · short bracket · return distribution · regime · setup quality ·
    crowding · mean reversion · data sufficient)          + news_return per headline
                   │
                   ▼
   parse.py: strict contract validation (errors are errors, never a "no")
                   │
                   ▼
   policy.py: EV in R = P(target)·RR − P(stop) − cost/stop, gates, risk sizing
                   │
                   ▼
   executor.py: Bybit TESTNET market entry + Full TP/SL on mark price → re-anchor to fill
                → read back the position → no stop confirmed = flatten immediately
                → time-stop at horizon · reduce-only exits · kill switch · daily loss limit
                   │
                   ▼
   SQLite (every request, answer, label, trade) → labeler → metrics → dashboard + report
```

## Quick start

Requirements: Python 3.12, and Node.js 20+ with npm (for the terminal UI).

```bash
cd ~/Projects/classifier-trading-bot

# 1. Python service (data, Kairos, execution, API)
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add KAIROS_API_KEY and Bybit TESTNET keys (or add them later in the UI: KEYS)

# 2. Terminal UI (Next.js): build once into web-next/out
cd web-next && npm ci && npm run build && cd ..

# 3. Start everything
.venv/bin/python -m ctb.server
```

Open http://127.0.0.1:8800. The Python service serves the built UI, the API and the live
`/ws` socket from that one address; there is no separate Node process to run.

### Working on the UI

| Task | Command | Notes |
|---|---|---|
| Rebuild after pulling or editing UI code | `cd web-next && npm run build` | Reload the page; no server restart needed once `web-next/out` existed at startup. The first build after a fresh clone needs one server restart so it is picked up. |
| Live-reload development | `cd web-next && npm run dev` | Open http://localhost:3000. Keep `.venv/bin/python -m ctb.server` running: the dev UI talks to its API and `/ws` on 127.0.0.1:8800 (the server allows only `localhost:3000` / `127.0.0.1:3000` cross-origin). |
| Type-check | `cd web-next && npx tsc --noEmit` | |

If `web-next/out` does not exist, the server falls back to the previous vanilla-JS terminal at `/`.
That one is always available at http://127.0.0.1:8800/legacy.

- Without `KAIROS_API_KEY` the dashboard still streams all market data and shows the exact
  state that would be sent. Get a key at https://minara.cloud. It must belong to a
  **Kairos-enabled group**: a `default`-group key may not reach `DMIND/Kairos`.
- The bot starts **observe-only**. Kairos decisions are made and scored, but no orders are placed
  until you switch on **AUTO-TRADE** in the command bar (or set `CTB_TRADING_ENABLED=1`).
- Kill switch: the dashboard button, or `touch KILL` in the project folder. It closes bot positions,
  cancels orders and halts.

### Smoke tests

```bash
.venv/bin/python -m pytest -q tests                       # unit tests (no network)
.venv/bin/python scripts/kairos_smoke.py SOLUSDT          # one real Kairos request + validation
.venv/bin/python scripts/testnet_smoke.py SOLUSDT long    # min-size testnet open → verify SL → close
```

## Why it is built this way

| Decision | Reason |
|---|---|
| Ask *which is hit first: +TP% or −SL% within 120 min* rather than *buy/sell/hold* | Every answer is labellable from price data, so calibration and skill are measurable. Code, not the model, decides. This is what the Kairos skill itself recommends. |
| 8 questions per request | Kairos V4 hard limit. The old Jev schema sent 15. |
| Relative, unit-labelled features | Raw MA levels like `ema_50: 332.10` waste tokens and say nothing. "0.6% below EMA50" does. The state stays at ~1.1k tokens against a 4096/question budget. |
| `null` until a window has data | After a restart, a 60m flow window holds 1 minute of trades. Reporting that as a 60-minute value misleads the model. |
| Mainnet data, testnet orders | Testnet flow/OI/funding/liquidations are synthetic. Testnet prices drift (BTC was +78bp off mainnet during development), so brackets are re-anchored to the testnet fill, and trading pauses above 150bp divergence. |
| Brackets from 15m ATR, min 40bp | With 5.5bp taker fees + slippage (≈15bp round trip), a 5m-ATR stop on BTC would pay ~0.6R in costs per trade. |
| Label everything, trade a subset | Model skill is measured on every bar (unbiased). Trading P&L measures the full system incl. fees and slippage. |
| Repeat + mirror probes (5% of bars) | Measures determinism, and whether a bullish⇄bearish mirrored market flips the answer (directional bias test). |

## Evaluation (Evaluation tab, `/api/metrics`, `Generate Minara report`)

- **Per question:** multiclass Brier / RPS vs climatology and uniform baselines, skill score,
  log loss, argmax accuracy vs majority class, AUC of P(target first), reliability diagrams,
  ECE, PIT histogram for the return distribution.
- **Edge:** does predicted EV rank realised R (Spearman, EV buckets)? Policy-signal mean R and
  win rate vs always-long / always-short baselines; realised R by setup quality.
- **Consistency:** repeat determinism, mirror symmetry, long/short coherence, self-contradictions.
- **Ops:** error rates by kind, contract violations, latency p50/p90/p99, tokens, retries, model versions.
- **Trading (testnet):** net P&L after fees, fees, win rate, profit factor, avg R vs predicted EV,
  max drawdown, exit reasons, by side and symbol.
- **news_return:** RPS skill, Spearman(up-minus-down lean, realised 15m return).

Labels use mainnet 1m bars, starting at the first bar that opens after the decision (no
look-ahead). A bar touching both target and stop counts as a stop (conservative, flagged).
Overlapping 2h horizons make consecutive decisions correlated; treat n as optimistic, and
wait for **several hundred labelled decisions per symbol** before drawing conclusions.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `CTB_SYMBOLS` | BTC,ETH,SOL,XRP,DOGE USDT | Perps to watch and trade |
| `CTB_DECISION_TF` / `CTB_HORIZON_BARS` | 5 / 24 | Decide every closed 5m bar; 2h bracket horizon and time-stop |
| `CTB_ATR_TF`, `CTB_SL_ATR`, `CTB_TP_ATR` | 15, 1.5, 2.5 | Bracket = 1.5×ATR(15m) stop, 2.5×ATR target (RR 1.67) |
| `CTB_MIN_SL_BP` / `CTB_MAX_SL_BP` | 40 / 300 | Stop distance clamp |
| `CTB_MIN_EDGE_R` / `CTB_MIN_SETUP_QUALITY` | 0.15 / 1.5 | Entry gates |
| `CTB_TAKER_FEE_BP` / `CTB_SLIPPAGE_BP` | 5.5 / 2 | Cost model used in EV and labels |
| `CTB_RISK_PER_TRADE_PCT` | 0.5 | % of equity lost if the stop is hit |
| `CTB_MAX_LEVERAGE` / `CTB_MAX_POSITIONS` / `CTB_MAX_NOTIONAL_USDT` | 3 / 3 / 5000 | Hard caps |
| `CTB_DAILY_LOSS_LIMIT_PCT` | 3 | Halts trading for the UTC day |
| `CTB_KAIROS_MAX_RPH` / `CTB_KAIROS_NEWS_MAX_RPH` | 120 / 20 | Request budgets (5 symbols × 12 bars/h = 60 + probes) |

## Safety

- The Bybit host is hard-coded to `api-testnet.bybit.com`. There is no mainnet switch.
- API keys are read from the environment only. They are never logged, stored, or sent to the browser.
  The server binds to 127.0.0.1.
- Use a testnet key with Contract → Orders/Positions only, no withdrawals.
- Positions you open by hand on testnet are shown, never touched by the bot (except the kill switch).

## Layout

```
ctb/config.py             settings (.env)
ctb/market/               Bybit WS/REST feed, in-memory store, news/macro/F&G context
ctb/features.py           state builder + mirror transform
ctb/indicators.py         pure-python TA
ctb/kairos/               client (contract), questions (versioned), parse (validation), news_return
ctb/policy.py             EV, gates, sizing
ctb/broker/               signed testnet client, executor
ctb/evaluation/           labeler, metrics, report
ctb/engine.py             orchestration
ctb/server.py             API + /ws live socket + serves the UI
web-next/                 terminal UI (Next.js, static export to web-next/out)
web/                      previous vanilla-JS terminal (/legacy)
web/                      dashboard (vanilla JS, lightweight-charts)
```

## Execution accuracy (testnet)

Bybit testnet books are thin and the testnet mark price follows mainnet while its book can trade
100+ bp away. Every number the bot records therefore comes from what Bybit actually filled:

- **Entries** are IOC limit orders capped `CTB_MAX_ENTRY_SLIP_BP` (10 bp) past the best testnet
  price and sized to the depth inside that cap. The filled quantity is what gets recorded.
- **Exits** (`CTB_EXIT_MODE=managed`, the default): the target is a resting reduce-only limit at the
  target price (fills at it or better); the stop is watched on the testnet bid/ask stream and
  exited with reduce-only IOC limits that widen `CTB_EXIT_CHASE_STEP_BP` per step. An exchange
  stop at `CTB_BACKSTOP_MULT` x the distance, triggered on mark price, covers the bot being down.
- **P&L, fees, exit price and R** are summed from every Bybit closed-P&L row of the exit (one exit
  can fill in several pieces) and R uses the stop that was actually live. `scripts/backfill_trades.py`
  re-books older trades the same way.
- Entry/exit slippage, fill ratio, entry latency and fill counts are stored per trade and shown in
  EVAL -> Execution quality and in the TRADES screen.

## Front end and latency

The terminal UI is a Next.js static export (`web-next/`, see its README) served at `/`. The browser
gets live data over one WebSocket (`/ws`) instead of polling. The bot itself listens to Bybit's
mainnet public stream, the testnet ticker stream and the testnet private stream (fills, positions,
wallet), and decides 0.3 s after each bar closes (`CTB_BAR_SETTLE_S`).

## Question set v2 and calibration (perp-v2.0)

v1 was scored on 145 labelled live decisions against the instrument's own base rates for the same
bracket: Kairos was worse than the base rates on every bracket question (Brier skill vs base rates
long -0.28, short -0.20, return distribution -0.15) and expected far more movement than happened.

- **Anchoring.** The state now carries `historical_base_rates`: how often target/stop/neither,
  each return bin, "higher at horizon" and "moved one stop" actually happened for this exact bracket
  on this instrument over the last ~40 h of completed windows (no look-ahead). Each question tells
  Kairos to start from those and move only as far as the evidence supports.
- **Questions.** long/short bracket, return distribution, direction, big_move, mean_reversion,
  setup_quality (7). `data_sufficient`, `regime` and `crowding` were dropped: none was labellable and
  `data_sufficient` (never above 0.53) blocked every strict trade.
- **Calibration.** Decisions use `w * Kairos + (1 - w) * base rates` per question. `w` starts at 0.5
  and is refitted every 15 min on labelled v2 outcomes once a question has 100 of them. If Kairos adds
  nothing, `w` goes to 0 and the bot stops trading on it. EVAL -> BASE shows Kairos vs base rates.
- **Strict gates (EXPLORE off).** Calibrated EV >= `CTB_MIN_EDGE_R`, setup_quality >= 1.5,
  calibrated direction agrees with the side by `CTB_MIN_DIRECTION_MARGIN`, return distribution
  agrees, base rates available, plus the hard gates.
- **Portfolio caps.** Total open stop-risk <= `CTB_MAX_OPEN_RISK_PCT` (2% of equity; new trades shrink
  to fit) and at most `CTB_MAX_SAME_SIDE` (4) positions in one direction.

Replaying the v1 answers through this policy on the same bars: raw Kairos would have taken 7 trades
averaging -0.39R; the calibrated strict policy takes none. With the default 5m profile the base
rates alone are never +EV (15 bp round trip is ~0.25R on a 60 bp stop), so strict trades only
happen when Kairos shows real information beyond the base rates.

## Symbol universe

`UNIV` (or SYMBOLS on the monitor panel) lists the top 100 USDT perps by 24h turnover with testnet
listing, testnet depth within 20 bp and spread. Only perps listed on both mainnet and testnet are
accepted (max 30). The Kairos budget is checked for the new count (raise it explicitly if needed),
a symbol with an open bot trade cannot be removed, and the choice is saved to `.env` (`CTB_SYMBOLS`).
The service then restarts itself (~15 s) and resumes auto-trade if it was on. Public market data
uses one WebSocket per 8 symbols; at most `CTB_KAIROS_CONCURRENCY` (6) Kairos requests run at once.

## Test runs

Every Kairos decision and testnet trade is stamped with the run that was active, so each test is
read on its own and next to the overall total (TRADES -> RUNS; EVAL -> run filter; status line).

| Run | Period (UTC, 10 Nov 2026) | Setup |
|---|---|---|
| 1 | 09:00 - 13:55 | v1 questions, EXPLORE, market entries, mark-price market TP/SL, 5 symbols |
| 2 | 13:55 - 14:33 | v1 questions, EXPLORE, IOC-capped entries, exact fill booking, managed exits from 14:12 |
| 3 | 14:34 - | v2 questions, calibrated strict policy (EXPLORE off), BTC ETH HYPE XRP SOXL SOL XAU |

A run stays open across restarts. A new run starts with `POST /api/runs/start` once no bot trade is
open (so P&L never straddles two runs); its configuration is snapshotted with it. Runs 1 and 2 were
defined from timestamps with `scripts/define_runs.py`.
