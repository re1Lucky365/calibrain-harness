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

```bash
cd ~/Projects/classifier-trading-bot
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add KAIROS_API_KEY and Bybit TESTNET keys
.venv/bin/python -m ctb.server
```

Open http://127.0.0.1:8800.

- Without `KAIROS_API_KEY` the dashboard still streams all market data and shows the exact
  state that would be sent. Get a key at https://minara.cloud. It must belong to a
  **Kairos-enabled group**: a `default`-group key may not reach `DMIND/Kairos`.
- The bot starts **observe-only**. Kairos decisions are made and scored, but no orders are placed
  until you flip **Testnet trading** in the header (or set `CTB_TRADING_ENABLED=1`).
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
ctb/server.py             API + SSE + static
web/                      dashboard (vanilla JS, lightweight-charts)
```
