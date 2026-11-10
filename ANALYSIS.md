# Analysis: Jev → Kairos, and what we can expect

## 1. What the Jev bot taught us (jev-trades review)

| Jev design | Problem | What this project does instead |
|---|---|---|
| 15 questions per call (`schema.py`) | Exceeds the Kairos V4 limit of 8 | 8 questions, each one labellable |
| "buy / hold / sell" action question | Can't be scored. The model makes the trade decision and the arithmetic is hidden in prose | Model predicts observable outcomes; code computes EV in R and applies gates |
| SL/TP buckets 0.75–12% on 1m bars | Far wider than 1m volatility; stops/targets rarely hit inside a sensible hold | ATR-scaled brackets with explicit horizon and costs in the state |
| Raw MA price levels in state | Tokens spent on numbers the model must subtract mentally | Relative %, bp and ratios with units in the key names |
| Spot, long-only | No short exposure, no derivatives context | Perps, long & short, funding/OI/liquidations/LS-ratio |
| Decision on every tick | Inputs barely change tick to tick; burns budget and makes duplicate decisions | One decision per closed 5m bar per symbol, plus news-triggered forecasts |
| TP/SL mode "TBD", local watch fallback | Process crash = unprotected position | Exchange-side Full TP/SL on mark price, read back and verified, flatten if missing |
| No outcome labelling | "Did well" was anecdotal | Every prediction scored against realised mainnet prices |

## 2. How good can this be? (honest expectation)

Kairos is a **judgment model over supplied text/JSON**. It does not see live data, does not
compute indicators, and its skill doc says `confidence` is not calibrated correctness and
Jev's calibration should not be assumed to transfer. So:

- The **upper bound** is how much predictable signal exists in the features we give it, at a
  2h horizon, after ~15bp round-trip costs. For liquid perps that edge is small. A useful
  model might score a **Brier skill of +1–5%** over climatology and a **Spearman(EV, realised R)
  of 0.05–0.15**. That is enough to filter trades. It will not produce a high win rate.
- The **most likely failure modes**, which the probes are built to catch: probabilities that barely
  move (a prior, not a reading of the state); a constant long or short bias (mirror test);
  inconsistent long vs short bracket answers (coherence); non-determinism (repeat test).
- **Costs dominate.** With BTC's 40bp minimum stop, costs are 0.37R per trade. The
  target-first probability must exceed ~52% for a positive EV on a 1.67 RR bracket. Watch
  `edge.by_predicted_ev`: if the realised R doesn't rise with predicted EV, the model is
  not ranking trades, whatever the P&L of a lucky week says.
- **Testnet P&L ≠ model quality.** Testnet fills, spreads and price paths are not mainnet's.
  Judge the model on the labelled metrics, and the execution layer on testnet P&L.

Decision rule I'd suggest before believing anything: **≥ 500 labelled decisions per symbol
(~2 days at 5 symbols), Brier skill > 0 on both brackets, and a monotone EV-bucket table.**
Only then raise risk or tune thresholds. Tune on one week, confirm on the next.

## 3. Feedback for Minara (pre-live; from the contract and integration work)

These come from building against the published skill (v0.1.5, commit 830ad0f, contract dated
2026-11-05). Live-measured findings are produced by **Evaluation → Generate Minara report**.

1. **Shared state is billed and tokenised per question.** Eight questions over a ~1.1k-token
   state ≈ 9–10k input tokens per decision, of which ~85% is the same state repeated. A
   request-level shared context (encode once, score N heads) would cut cost ~6× for
   multi-question use, which is the pattern the docs recommend.
2. **8 questions / 4096 tokens per question** force trade-offs. Either limit should be raised,
   or the docs should give guidance on which is binding for typical numeric states.
3. **No published rate limits, pricing or latency SLO**, and an upstream wait budget of
   600 s. A trading loop needs to know p99 latency and a requests/minute ceiling to budget.
4. **Calibration is undocumented for financial outcomes.** Publish reliability curves
   per task profile, and say what `calibration_version` means.
5. **Always return `model_version`, `prompt_version` and `calibration_version`** (now optional).
   Without them an evaluation can't be pinned to a model and silent upgrades break comparisons.
6. **Determinism is undocumented.** Is the same request guaranteed the same answer? Is there a seed?
   (The repeat probe measures it.)
7. **Three error shapes** (`detail{}`, `error{}`, `error.message`) make clients brittle. Pick one.
8. **news_return:** the caller must rewrite candidate descriptions from `round_trip_cost_bp`
   exactly, or the request is invalid. The server should generate them from the state field.
   Also: no crypto-specific `asset_class`/`event_type` vocabulary is documented (the example
   uses Chinese labels for US stocks), there's no representative value per bin, and only
   15m/1h/4h horizons are offered.
9. **Numeric input guidance.** Document how numbers are tokenised and whether fewer
   decimals, percentages, or bucketed descriptions help, since most trading state is numeric.
10. **Training cutoff / leakage.** State the data cutoff so users know backtests on earlier
    periods may be contaminated. This project evaluates forward only for that reason.
