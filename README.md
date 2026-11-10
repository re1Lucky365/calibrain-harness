<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/calibrain-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/brand/calibrain-logo-light.svg">
    <img alt="Calibrain" src="docs/brand/calibrain-logo-light.svg" width="440">
  </picture>
</p>

<p align="center">
  <b>Trade crypto perpetuals with fast classification models, and find out honestly whether they have an edge.</b>
</p>

<p align="center">
  <img alt="status: under development" src="https://img.shields.io/badge/status-under%20development-ff9a2e?style=flat-square">
  <img alt="venue: Bybit testnet only" src="https://img.shields.io/badge/venue-Bybit%20testnet%20only-3a3a3a?style=flat-square">
  <img alt="models: Kairos" src="https://img.shields.io/badge/models-Kairos-3a3a3a?style=flat-square">
  <img alt="python 3.12" src="https://img.shields.io/badge/python-3.12-3a3a3a?style=flat-square">
  <img alt="next.js 16" src="https://img.shields.io/badge/next.js-16-3a3a3a?style=flat-square">
</p>

> [!WARNING]
> **Calibrain is under active development.** It trades on the **Bybit testnet only** (the host is
> hard-coded; there is no mainnet switch). Interfaces, question sets and defaults change between
> commits. Nothing here is financial advice.

<p align="center">
  <a href="docs/media/calibrain-demo.mp4"><img alt="Calibrain terminal demo (click for the full video)" src="docs/media/calibrain-demo.gif" width="880"></a>
  <br><sub>Live terminal: monitor, Kairos signals, evaluation and news. <a href="docs/media/calibrain-demo.mp4">Watch the full 1-minute video</a>.</sub>
</p>

## What it is

Calibrain connects a **high-speed classification model** to perpetual futures and treats every answer
as a forecast that will be checked. On each closed bar it asks the model a small set of
**labellable questions** ("which is touched first in the next 2 h: +1.0% or −0.6%?"), turns the
probabilities into expected value in code, trades the ones that clear the gates on Bybit testnet
(long and short, with protected stops and targets), and then scores **every** prediction against
what the market actually did, traded or not.

You get two things: a trading bot, and an honest evaluation of the model behind it.

### Supported models

Calibrain is built for any fast classifier that returns calibrated probabilities over a fixed set
of options, such as **Kairos**, **Jev** and similar models. Right now only **Kairos** is supported.

| Model | Status |
|---|---|
| **Kairos** (`DMIND/Kairos` on [Minara Cloud](https://minara.cloud)) | Supported |
| Jev | Planned |
| Other multiple-choice probability models | Planned, via a model adapter |

## Features

- **Bloomberg-style terminal.** Next.js front end fed by one WebSocket: candles, order book with
  depth chart and wall detection, market map, liquidation and block tape, derivatives and flow, the
  Kairos signal with calibrated vs raw expected value, and a decision blotter with the full request
  and response for every call.
- **Labellable question set** with the instrument's own **base rates** in the state, so the model
  starts from how often each outcome really happens and is measured against that.
- **Calibration layer.** Decisions blend the model with the base rates; the weight is fitted on
  labelled outcomes, so a model that adds nothing stops driving trades.
- **Exact execution accounting on thin testnet books.** IOC-capped entries, resting take-profits,
  mainnet-confirmed managed stops with capped IOC exits, and P&L, fees and R summed from every
  Bybit closed-P&L row.
- **Evaluation built in.** Brier/RPS skill vs climatology and vs the base rates the model was shown,
  AUC, reliability diagrams, PIT, EV ranking, repeat and mirror consistency probes, execution
  quality, and per-run results (Run 1, Run 2, …, Overall).
- **Safety.** Testnet-only host, keys only in `.env`, kill switch, daily loss limit, per-trade,
  portfolio and same-direction risk caps, venue-divergence gate.

## Quick start

Requirements: Python 3.12, Node.js 20+ with npm, a Kairos API key and Bybit **testnet** API keys.

```bash
git clone https://github.com/zadescoxp/classifier-trading-terminal.git calibrain
cd calibrain

# 1. Python service (data, model calls, execution, API)
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add keys here, or later in the terminal (KEYS)

# 2. Terminal UI (Next.js): build once into web-next/out
cd web-next && npm ci && npm run build && cd ..

# 3. Start everything
.venv/bin/python -m ctb.server
```

Open http://127.0.0.1:8800. The Python service serves the built UI, the API and the live `/ws`
socket from that one address; there is no separate Node process to run.

- **Keys.** Kairos keys come from [minara.cloud](https://minara.cloud) and must belong to a
  Kairos-enabled group. Bybit testnet keys come from
  [testnet.bybit.com → API management](https://testnet.bybit.com/app/user/api-management) with
  Contract → Orders + Positions and **no withdrawal permission**.
- **Observe-only first.** The bot starts without placing orders. Decisions are made and scored;
  switch on **AUTO-TRADE** in the command bar to trade on testnet.
- **Kill switch.** The KILL button, or `touch KILL` in the project folder: closes bot positions,
  cancels orders and halts.

### Working on the UI

| Task | Command | Notes |
|---|---|---|
| Rebuild after pulling or editing UI code | `cd web-next && npm run build` | Reload the page. The first build after a fresh clone needs one server restart so it is picked up. |
| Live-reload development | `cd web-next && npm run dev` | Open http://localhost:3000 with the Python server still running; the dev UI talks to its API and `/ws` on 127.0.0.1:8800. |
| Type-check | `cd web-next && npx tsc --noEmit` | |

Without `web-next/out` the server falls back to the earlier vanilla-JS terminal, which is also
always at http://127.0.0.1:8800/legacy.

### Tests and smoke checks

```bash
.venv/bin/python -m pytest -q tests                       # unit tests (no network)
.venv/bin/python scripts/kairos_smoke.py SOLUSDT          # one real Kairos request + contract validation
.venv/bin/python scripts/testnet_smoke.py SOLUSDT long    # min-size testnet open -> verify stop -> close
```

## Using the terminal

| Screen | Key | What it shows |
|---|---|---|
| MON | F1 | Watchlist, chart with model markers, Kairos signal, account and positions, relative performance, market map, order book, news, liquidations, derivatives/flow/trend, decision blotter |
| EVAL | F2 | Findings, calibration, EV ranking, PIT, per-question scores, Kairos vs base rates, probes, operations, testnet P&L, execution quality; filter by run, symbol, question set, timeframe |
| TRADES | F3 | Runs table (Run 1, 2, 3 … and Overall) and every trade with reference price, slippage, fills and exact R |
| NEWS | F4 | Model news_return forecasts and headlines |
| LOG | F5 | System events |

Command line (press `/`): `BTC`, `ETH` … load a symbol · `ASK` / `PRB` ask the model now (with
consistency probes) · `1M`–`4H` chart timeframe · `TF 5M` trading timeframe · `UNIV` choose symbols
· `POS 10` max positions · `KEYS` · `HELP`.

## How it works

```
Bybit mainnet public data (sharded WS + REST)        news RSS · Fear & Greed · macro
  klines 1m–4h, tickers, funding, OI, long/short            │
  accounts, liquidations, taker trades, order book          │
        │                                                   │
        └──► features.py: compact, unit-labelled state + historical base rates for the bracket
                   │
                   ▼
   model (Kairos /v1/systemone): 7 labellable questions per closed bar per symbol
   long bracket · short bracket · return distribution · direction · big move · mean reversion · setup quality
                   │
                   ▼
   parse.py: strict contract validation (an error is an error, never a "no")
                   │
                   ▼
   calibration.py: w · model + (1 − w) · base rates, w fitted on labelled outcomes
   policy.py: EV in R = P(target)·RR − P(stop) − cost/stop, gates, risk sizing, portfolio caps
                   │
                   ▼
   executor.py (Bybit TESTNET): IOC-capped entry → bracket anchored to the fill → resting take-profit
                → managed stop triggered on mainnet, exited with capped IOC → mark-price backstop
                → time-stop · reduce-only exits · kill switch · daily loss limit
                   │
                   ▼
   SQLite (every request, answer, label, trade, run) → labeler → metrics → terminal + report
```

## Results so far

Live forward tests on 10 Nov 2026 (5m decisions, 2 h horizon, Bybit testnet). P&L is from Bybit's
own closed-P&L records, after fees. Model quality is scored on mainnet prices for every decision.

| Run | Setup | Hours | Model decisions | Trades | Net P&L (USDT) |
|---|---|---|---|---|---|
| 1 | v1 questions · explore · market entries | 3.9 | 235 | 18 | −2,233.49 |
| 2 | v1 questions · explore · IOC entries, managed exits | 0.5 | 35 | 7 | −359.05 |
| 3 (strict) | v2 questions · calibrated strict policy · EXPLORE off | 3.75 | 322 | **0** | 0 |
| 3 (explore) | v2 questions · EXPLORE on | 2.0 | 177 | 15 | −8.48 |
| **All** | | 10.5 | 769 (3 errors) | 40 | **−2,601.01** |

What we learned:

- **Testnet liquidity dominates testnet P&L.** Run 1's −2,041 USDT on SOL came from market orders
  filling 3–11% away from the price on thin testnet books. Capped IOC entries brought entry
  slippage to ~0–4 bp; testnet-only crashes of 4–6% are now handled by mainnet-confirmed stops.
- **v1 questions lost to simple base rates;** v2, which shows the model those base rates, beats them
  on the short bracket, direction and big-move questions (Brier skill vs base rates +0.09, +0.17,
  +0.11 over 320 strict-mode decisions) and is level on the long bracket (−0.03).
- **Strict mode took no trades in 3 h 45 m.** With a 15 bp round trip costing ~0.25R on these
  stops, the base rates are never positive-EV on their own, so a strict trade needs the model to be
  clearly more confident than the base rates. That is the point of strict mode, and the reason
  exploration mode exists for collecting trade data.

Read these as an early sample: one day, one market regime, correlated symbols and overlapping
horizons.

## Design choices

| Choice | Reason |
|---|---|
| Ask *which is hit first within the horizon* instead of *buy / sell / hold* | Every answer is labellable from price data, so calibration and skill are measurable. Code, not the model, decides. |
| Show the model the instrument's base rates | v1 forecasts expected far more movement than happened. Anchoring on measured frequencies fixed most of that, and gives a fair baseline to beat. |
| 7 questions per request | Kairos allows up to 8; each question carries the full state, so fewer questions means fewer tokens and lower latency. |
| Relative, unit-labelled features | "0.6% below EMA50" is informative; a raw MA level is not. The state stays well inside the per-question context budget. |
| `null` until a window has data | After a restart a 60-minute flow window holds one minute of trades; reporting it as 60 minutes would mislead the model. |
| Mainnet signals, testnet orders | Testnet flow, OI and funding are synthetic and its prices drift, so signals and labels use mainnet and orders are sized to the testnet book. |
| Label everything, trade a subset | Model skill is measured on every bar (unbiased); trading P&L measures the full system with fees and slippage. |
| Repeat and mirror probes | Measure determinism, and whether a mirrored (bullish ⇄ bearish) market flips the answer. |

## Evaluation

- **Per question:** multiclass Brier / RPS vs climatology and uniform, skill scores, log loss,
  accuracy vs majority class, AUC, reliability diagrams, ECE, PIT for the return distribution.
- **Vs base rates:** Brier of the model, of the base rates it was shown, and of the best blend.
- **Edge:** does predicted EV rank realised R (Spearman, EV buckets)? Signal results vs
  always-long / always-short.
- **Consistency:** repeat determinism, mirror symmetry, long/short coherence.
- **Operations:** errors by kind, contract violations, latency, tokens, retries.
- **Trading and execution:** net P&L after fees, win rate, profit factor, R, drawdown, exit reasons,
  entry and exit slippage, fill ratio, entry latency.
- **news_return:** RPS skill and lean vs realised 15-minute return.

Labels use mainnet 1m bars from the first bar after the decision (no look-ahead). A bar touching
both target and stop counts as a stop. Consecutive decisions overlap, so treat sample sizes as
optimistic and wait for several hundred labelled decisions per symbol before drawing conclusions.
EVAL → EXPORT MINARA REPORT writes a Markdown report.

## Execution on testnet

- **Entries:** IOC limit orders capped `CTB_MAX_ENTRY_SLIP_BP` past the best testnet price, sized to
  the depth inside the cap. The filled quantity is what is recorded.
- **Exits** (`CTB_EXIT_MODE=managed`): the target rests as a reduce-only limit (fills at it or better).
  The stop is evaluated on **mainnet's** executable price from the mainnet decision price; when it
  is crossed the position is exited on testnet with reduce-only IOC orders widening
  `CTB_EXIT_CHASE_STEP_BP` per step. A testnet-only crossing is logged as a dislocation and held.
  An exchange stop at `CTB_BACKSTOP_MULT` × the distance on mark price covers the bot being down.
- **Booking:** P&L, fees, exit price and R are summed from every Bybit closed-P&L row of the exit,
  with R measured against the stop that was really live.

## Question set v2 and calibration

- **Anchoring.** The state carries `historical_base_rates`: how often each outcome happened for the
  exact bracket on this instrument over the last ~33–40 h of completed windows.
- **Questions.** Long/short bracket, return distribution, direction, big move, mean reversion,
  setup quality.
- **Calibration.** `w · model + (1 − w) · base rates` per question; `w` starts at 0.5 and is refitted
  every 15 minutes once a question has 100 labelled outcomes.
- **Strict gates.** Calibrated EV ≥ `CTB_MIN_EDGE_R`, setup quality ≥ `CTB_MIN_SETUP_QUALITY`,
  calibrated direction agreeing with the side by `CTB_MIN_DIRECTION_MARGIN`, return distribution
  agreeing, plus the hard gates (existing position, venue divergence, shorts disabled).
- **Exploration** (`CTB_EXPLORATION=1`) also trades the model's preferred side when the strict gates
  block it, at reduced risk, to collect real fills on its directional lean.

## Symbols and runs

- **Universe.** `UNIV` lists the top 100 USDT perps by turnover with testnet listing, depth and
  spread, and flags thin books and stock/commodity perps. Up to 30 symbols; the model request budget
  is checked; the service restarts itself to apply the change.
- **Runs.** Every decision and trade is stamped with the active run. A new run starts with
  `POST /api/runs/start` once no bot trade is open, and snapshots its configuration.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `CTB_SYMBOLS` | see `.env.example` | Perps to watch and trade (or use `UNIV`) |
| `CTB_DECISION_TF` / `CTB_HORIZON_BARS` / `CTB_ATR_TF` | 5 / 24 / 15 | Decide on every closed 5m bar; 2 h horizon and time-stop; stop sized from 15m ATR |
| `CTB_SL_ATR` / `CTB_TP_ATR` | 1.5 / 2.5 | Stop and target in ATR multiples |
| `CTB_MIN_SL_BP` / `CTB_MAX_SL_BP` | 40 / 300 | Stop distance clamp |
| `CTB_MIN_EDGE_R` / `CTB_MIN_SETUP_QUALITY` / `CTB_MIN_DIRECTION_MARGIN` | 0.15 / 1.5 / 0.03 | Strict entry gates |
| `CTB_EXPLORATION` / `CTB_EXPLORE_RISK_PCT` | 0 / 0.1 | Exploration mode and its risk per trade |
| `CTB_TAKER_FEE_BP` / `CTB_SLIPPAGE_BP` | 5.5 / 2 | Cost model used in EV and labels |
| `CTB_RISK_PER_TRADE_PCT` | 0.5 | % of equity lost if a stop is hit |
| `CTB_MAX_POSITIONS` / `CTB_MAX_SAME_SIDE` / `CTB_MAX_OPEN_RISK_PCT` | 3 (10 in `.env.example`) / 4 / 2 | Portfolio caps |
| `CTB_MAX_LEVERAGE` / `CTB_MAX_NOTIONAL_USDT` / `CTB_DAILY_LOSS_LIMIT_PCT` | 3 / 5000 / 3 | Hard caps |
| `CTB_MAX_ENTRY_SLIP_BP` / `CTB_EXIT_MODE` | 10 / managed | Entry price cap and exit handling |
| `CTB_KAIROS_MAX_RPH` / `CTB_KAIROS_CONCURRENCY` | 120 / 6 | Model request budget and parallelism |

## Safety

- The Bybit host is hard-coded to `api-testnet.bybit.com`. There is no mainnet switch.
- Keys are read from `.env` only (owner-only permissions, gitignored). They are never logged,
  stored in the database or sent to the browser, which only sees the last four characters.
- The server binds to 127.0.0.1 and refuses cross-origin writes.
- Positions opened by hand on testnet are shown and never touched by the bot (except the kill switch).

## Project layout

```
ctb/                      Python service (internal package name from the project's first version)
  config.py               settings from .env
  market/                 Bybit mainnet/testnet streams, store, news/macro context
  features.py             state builder, base rates, mirror transform
  kairos/                 model client, versioned question sets, strict parsing, news_return
  calibration.py          model vs base-rate blending and fitting
  policy.py               EV, gates, sizing
  broker/                 signed testnet client, private stream, executor
  evaluation/             labeler, metrics, report
  runs.py · universe.py   test runs, symbol validation
  engine.py · server.py   orchestration, API, /ws, serves the UI
web-next/                 terminal UI (Next.js 16, static export to web-next/out)
web/                      earlier vanilla-JS terminal (/legacy)
scripts/                  smoke tests, backfills, run definitions
docs/                     brand assets and demo media
```

## Roadmap

- Model adapters so Jev and other multiple-choice probability models can be plugged in next to Kairos
- Calibration that counts independent bars rather than decisions, with shrinkage toward the base rates
- Limit-based exchange backstops so a testnet liquidity hole cannot fill a disaster stop
- Longer timeframes and wider brackets, where costs are a smaller share of the stop
- Multi-day, multi-regime evaluation runs and published reports

## Contributing

Issues and pull requests are welcome while the project is under development. Please run
`.venv/bin/python -m pytest -q tests` and `cd web-next && npx tsc --noEmit` before opening a PR, and
never commit `.env` or anything under `data/`.

## Disclaimer

Calibrain is research software for testnet forward-testing. It is not investment advice, it has not
been audited, and past testnet results say nothing reliable about future returns.
