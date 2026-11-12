enterprise-security-platform/
├── ... (existing networking, crypto, iam, threat, devsecops, compliance modules)
├── credential-vault-sso/       # Zero-knowledge global password & WebAuthn/Passkey manager
├── time-tracking-ledger/       # Automated activity tracking, billing & immutable timelines
└── docker-compose.yml          # Master orchestration to launch all platform services
import os
import json
import time
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

class ZeroKnowledgeCredentialManager:
    def __init__(self):
        self.vault_records = {}
        self.registered_passkeys = {}

    def _derive_master_key(self, master_password: str, salt: bytes) -> bytes:
        """Derives a 256-bit encryption key from a master password using Scrypt."""
        kdf = Scrypt(salt=salt, length=32, n=2**17, r=8, p=1)
        return kdf.derive(master_password.encode('utf-8'))

    def store_account_credential(self, account_id: str, service_name: str, username: str, secret_password: str, master_password: str) -> dict:
        """Encrypts and stores account credentials using Zero-Knowledge AES-256-GCM."""
        salt = os.urandom(16)
        nonce = os.urandom(12)
        derived_key = self._derive_master_key(master_password, salt)
        aesgcm = AESGCM(derived_key)

        payload = json.dumps({
            "service": service_name,
            "username": username,
            "password": secret_password,
            "updated_at_utc": time.time()
        }).encode('utf-8')

        ciphertext = aesgcm.encrypt(nonce, payload, account_id.encode('utf-8'))
        
        record = {
            "account_id": account_id,
            "service_name": service_name,
            "salt": salt.hex(),
            "nonce": nonce.hex(),
            "encrypted_blob": ciphertext.hex()
        }
        self.vault_records[account_id] = record
        return {"status": "ENCRYPTED_AND_STORED", "account_id": account_id, "service": service_name}

    def retrieve_account_credential(self, account_id: str, master_password: str) -> dict:
        """Decrypts an account record locally upon valid master key derivation."""
        record = self.vault_records.get(account_id)
        if not record:
            raise ValueError("Account record not found.")

        derived_key = self._derive_master_key(master_password, bytes.fromhex(record["salt"]))
        aesgcm = AESGCM(derived_key)
        
        decrypted_bytes = aesgcm.decrypt(
            bytes.fromhex(record["nonce"]),
            bytes.fromhex(record["encrypted_blob"]),
            account_id.encode('utf-8')
        )
        return json.loads(decrypted_bytes.decode('utf-8'))

    def register_fido2_passkey(self, user_id: str, device_name: str, public_key_pem: str) -> dict:
        """Registers a hardware/biometric passkey (FIDO2/WebAuthn) for passwordless SSO."""
        credential_id = hashlib.sha256(f"{user_id}:{device_name}:{time.time()}".encode()).hexdigest()
        self.registered_passkeys[credential_id] = {
            "user_id": user_id,
            "device_name": device_name,
            "public_key": public_key_pem,
            "biometric_enforced": True,
            "status": "ACTIVE"
        }
        return {"credential_id": credential_id, "device": device_name, "sso_ready": True}
import hashlib
import json
import datetime

class ImmutableTimelineLedger:
    def __init__(self):
        self.chain = []
        self._create_genesis_entry()

    def _create_genesis_entry(self):
        genesis = {
            "index": 0,
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "actor_badge": "SYSTEM-ROOT",
            "category": "LEDGER_INIT",
            "task_description": "Initialized Immutable Enterprise Timeline",
            "duration_minutes": 0,
            "previous_hash": "0" * 64
        }
        genesis["entry_hash"] = self._hash_entry(genesis)
        self.chain.append(genesis)

    @staticmethod
    def _hash_entry(entry: dict) -> str:
        entry_copy = {k: v for k, v in entry.items() if k != "entry_hash"}
        encoded = json.dumps(entry_copy, sort_keys=True).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def log_automated_session(self, actor_badge: str, category: str, task_description: str, duration_minutes: float) -> dict:
        """Records a verified time-tracking entry linked to the previous cryptographic hash."""
        previous_entry = self.chain[-1]
        new_entry = {
            "index": len(self.chain),
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "actor_badge": actor_badge,
            "category": category,
            "task_description": task_description,
            "duration_minutes": round(duration_minutes, 2),
            "previous_hash": previous_entry["entry_hash"]
        }
        new_entry["entry_hash"] = self._hash_entry(new_entry)
        self.chain.append(new_entry)
        return new_entry

    def verify_timeline_integrity(self) -> bool:
        """Audits the entire timeline to confirm zero tampering or altered timestamps."""
        for i in range(1, len(self.chain)):
            current = self.chain[i]
            previous = self.chain[i - 1]
            if current["previous_hash"] != previous["entry_hash"]:
                return False
            if current["entry_hash"] != self._hash_entry(current):
                return False
        return True
version: '3.9'

services:
  vault-kms:
    image: hashicorp/vault:latest
    container_name: enterprise-kms-vault
    cap_add:
      - IPC_LOCK
    environment:
      VAULT_DEV_ROOT_TOKEN_ID: ${VAULT_ROOT_TOKEN}
      VAULT_ADDR: "http://0.0.0"
    ports:
      - "127.0.0.1:8200:8200"
    networks:
      - zero_trust_internal

  credential-sso-service:
    build: ./credential-vault-sso
    container_name: enterprise-credential-manager
    environment:
      VAULT_ADDR: "http://vault-kms:8200"
      ENFORCE_WEBAUTHN: "true"
    depends_on:
      - vault-kms
    networks:
      - zero_trust_internal

  timeline-ledger-service:
    build: ./time-tracking-ledger
    container_name: enterprise-time-tracker
    volumes:
      - timeline_data:/app/ledger_storage
    networks:
      - zero_trust_internal

  threat-detector-ai:
    build: ./threat-detectors
    container_name: enterprise-ai-threat-engine
    environment:
      PRIVACY_MODE: "GDPR_CCPA_STRICT"
      PII_RETENTION: "false"
    networks:
      - zero_trust_internal

networks:
  zero_trust_internal:
    driver: bridge
    internal: true

volumes:
  timeline_data:
main.pymain.py
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/calibrain-harness-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/brand/calibrain-harness-logo-light.svg">
    <img alt="Calibrain Harness" src="docs/brand/calibrain-harness-logo-light.svg" width="420">
  </picture>
</p>

<p align="center">
  <b>A forward-testing harness for trading with fast classification models: plug a model in, run it on live perpetual markets, and find out honestly whether it has an edge.</b>
</p>

<p align="center">
  <img alt="status: under development" src="https://img.shields.io/badge/status-under%20development-ff9a2e?style=flat-square">
  <img alt="venue: Bybit testnet only" src="https://img.shields.io/badge/venue-Bybit%20testnet%20only-3a3a3a?style=flat-square">
  <img alt="models: Kairos | Jev" src="https://img.shields.io/badge/models-Kairos%20%7C%20Jev-3a3a3a?style=flat-square">
  <img alt="python 3.12" src="https://img.shields.io/badge/python-3.12-3a3a3a?style=flat-square">
  <img alt="next.js 16" src="https://img.shields.io/badge/next.js-16-3a3a3a?style=flat-square">
  <a href="LICENSE"><img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-3a3a3a?style=flat-square"></a>
</p>

> [!WARNING]
> **Calibrain Harness is under active development.** It trades on the **Bybit testnet only** (the host is
> hard-coded; there is no mainnet switch). Interfaces, question sets and defaults change between
> commits. Nothing here is financial advice.

<p align="center">
  <a href="docs/media/calibrain-harness-demo.mp4"><img alt="Calibrain Harness terminal demo (click for the full video)" src="docs/media/calibrain-harness-demo.gif" width="880"></a>
  <br><sub>Live terminal: monitor, Kairos signals, evaluation and news. <a href="docs/media/calibrain-harness-demo.mp4">Watch the full 1-minute video</a>.</sub>
</p>

## What it is

Calibrain Harness connects a **high-speed classification model** to perpetual futures and treats every answer
as a forecast that will be checked. On each closed bar it asks the model a small set of
**labellable questions** ("which is touched first in the next 2 h: +1.0% or −0.6%?"), turns the
probabilities into expected value in code, trades the ones that clear the gates on Bybit testnet
(long and short, with protected stops and targets), and then scores **every** prediction against
what the market actually did, traded or not.

You get two things: a trading bot, and an honest evaluation of the model behind it.

### Supported models

Calibrain Harness is built for any fast classifier that returns calibrated probabilities over a fixed set
of options. **Kairos** and **Jev** are supported today; switch between them live with **MODEL** in the
terminal's function-key bar.

| Model | Provider | Model id | Key | Status |
|---|---|---|---|---|
| **Kairos** | DMind via [Minara Cloud](https://minara.cloud) | `DMIND/Kairos` | `KAIROS_API_KEY` | Supported (also powers news_return forecasts) |
| **Jev** | [TypeSafe](https://docs.typesafe.ai) | `jev-1.14.0` (pinned; override with `CTB_JEV_MODEL`) | `TYPESAFE_API_KEY` | Supported |
| Other System One style models | | | | Add a `ModelSpec` in `ctb/models.py` |

Both are called the same way (`POST /v1/systemone` with a state and typed questions), so the question
set, validation, calibration, policy and evaluation are shared. What is kept **per model**: every
decision is stamped with the model that made it, calibration weights are fitted per model (and per
trading timeframe), EVAL and reports filter by model, and switching models starts a new run (only
while the bot is flat), so results never mix two models.

## Features

- **Bloomberg-style terminal.** Next.js front end fed by one WebSocket: candles, order book with
  depth chart and wall detection, market map, liquidation and block tape, derivatives and flow, the
  model signal with calibrated vs raw expected value, and a decision blotter with the full request
  and response for every call.
- **Multiple models.** Kairos and Jev behind one interface, switchable live, evaluated separately.
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

Requirements: Python 3.12, Node.js 20+ with npm, a Kairos or Jev API key, and Bybit **testnet** API keys.

```bash
git clone https://github.com/zadescoxp/calibrain-harness.git
cd calibrain-harness

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
  Kairos-enabled group. Jev keys come from the [TypeSafe console](https://console.typesafe.ai/keys).
  Choose the model with `CTB_MODEL_PROVIDER=kairos|jev` or MODEL in the terminal. Bybit testnet keys come from
  [testnet.bybit.com → API management](https://testnet.bybit.com/app/user/api-management) with
  Contract → Orders + Positions and **no withdrawal permission**.
- **Observe-only first.** The bot starts without placing orders. Decisions are made and scored;
  switch on **AUTO-TRADE** in the command bar to trade on testnet.
- **Kill switch.** The KILL button, or `touch KILL` in the project folder: closes bot positions,
  cancels orders and halts.

### Working on the UI

| Task | Command | Notes |
|---|---|---|
| Rebuild after pulling or editing UI code | `cd web-next && npm run build` | Reload the page; no server restart needed. |
| Live-reload development | `cd web-next && npm run dev` | Open http://localhost:3000 with the Python server still running; the dev UI talks to its API and `/ws` on 127.0.0.1:8800. |
| Type-check | `cd web-next && npx tsc --noEmit` | |

If the UI has not been built yet, http://127.0.0.1:8800 shows a short page with the build command;
the API and the live socket work either way.

### Tests and smoke checks

```bash
.venv/bin/python -m pytest -q tests                       # unit tests (no network)
.venv/bin/python scripts/kairos_smoke.py SOLUSDT          # one real request to the active model + validation
.venv/bin/python scripts/kairos_smoke.py SOLUSDT --model jev   # same, against a specific model
.venv/bin/python scripts/testnet_smoke.py SOLUSDT long    # min-size testnet open -> verify stop -> close
```

## Using the terminal

| Screen | Key | What it shows |
|---|---|---|
| MON | F1 | Watchlist, chart with model markers, model signal, account and positions, relative performance, market map, order book, news, liquidations, derivatives/flow/trend, decision blotter |
| EVAL | F2 | Findings, calibration, EV ranking, PIT, per-question scores, model vs base rates, probes, operations, testnet P&L, execution quality; filter by model, run, symbol, question set, timeframe |
| TRADES | F3 | Runs table (Run 1, 2, 3 … and Overall) and every trade with reference price, slippage, fills and exact R |
| NEWS | F4 | Kairos news_return forecasts (Kairos only) and headlines |
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
   model (Kairos or Jev, /v1/systemone): 7 labellable questions per closed bar per symbol
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
| 7 questions per request | Kairos allows up to 8 (Jev limits by tokens: 64k per request, 32k per question); each question carries the full state, so fewer questions means fewer tokens and lower latency. |
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
| `CTB_MODEL_PROVIDER` | kairos | Which model decides: `kairos` or `jev` (or MODEL in the terminal) |
| `KAIROS_API_KEY` / `KAIROS_BASE_URL` | – / https://minara.cloud | Kairos credentials |
| `TYPESAFE_API_KEY` / `CTB_JEV_MODEL` | – / jev-1.14.0 | Jev credentials and pinned model version |
| `CTB_KAIROS_MAX_RPH` / `CTB_KAIROS_CONCURRENCY` | 120 / 6 | Model request budget and parallelism (any model) |

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
  models.py               supported models (Kairos, Jev): endpoint, model id, key, limits, retries
  kairos/                 System One client, versioned question sets, strict parsing, news_return
  calibration.py          model vs base-rate blending and fitting
  policy.py               EV, gates, sizing
  broker/                 signed testnet client, private stream, executor
  evaluation/             labeler, metrics, report
  runs.py · universe.py   test runs, symbol validation
  engine.py · server.py   orchestration, API, /ws, serves the UI
web-next/                 terminal UI (Next.js 16, static export to web-next/out)
scripts/                  smoke tests, backfills, run definitions
docs/                     brand assets and demo media
```

## Roadmap

- More models next to Kairos and Jev, and side-by-side runs of two models on the same bars
- Calibration that counts independent bars rather than decisions, with shrinkage toward the base rates
- Limit-based exchange backstops so a testnet liquidity hole cannot fill a disaster stop
- Longer timeframes and wider brackets, where costs are a smaller share of the stop
- Multi-day, multi-regime evaluation runs and published reports

## Contributing

Issues and pull requests are welcome while the project is under development. Please run
`.venv/bin/python -m pytest -q tests` and `cd web-next && npx tsc --noEmit` before opening a PR, and
never commit `.env` or anything under `data/`.

## Disclaimer

Calibrain Harness is research software for testnet forward-testing. It is not investment advice, it has not
been audited, and past testnet results say nothing reliable about future returns.

## License

[MIT](LICENSE). Use, modify and redistribute freely; the software comes with no warranty.
