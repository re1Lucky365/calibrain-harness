"""One-off: split the data recorded before runs existed into Run 1 and Run 2 (10 Nov 2026).

Run 1: first Kairos decisions (09:55 UTC) until the kill at 13:04; v1 questions, exploration,
       market entries, Full-mode TP/SL on mark price.
Run 2: 13:55 UTC (accuracy fixes deployed, trading re-enabled) until Run 3 starts; v1 questions,
       exploration, IOC-capped entries, exact fill booking, managed exits from 14:12.
Run 2 is left open; the engine closes it when it starts Run 3 (or POST /api/runs/start).

Usage: .venv/bin/python scripts/define_runs.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctb.config import load_settings  # noqa: E402
from ctb.db import DB  # noqa: E402
from ctb.runs import define_history  # noqa: E402


def ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()


s = load_settings()
db = DB(s.db_path)
if db.one("SELECT id FROM runs LIMIT 1"):
    sys.exit("runs already defined; nothing to do")
five = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
base = {"symbols": five, "decision_tf": "5", "horizon_minutes": 120, "qset": "perp-v1.0", "exploration_mode": True,
        "risk_per_trade_pct": 0.5, "explore_risk_pct": 0.1, "min_edge_r": 0.15}
define_history(db, [
    ("Run 1 · v1 · explore · market entries", ts("2026-11-10 09:00"), ts("2026-11-10 13:55"),
     {**base, "exit_mode": "exchange (mark-price market TP/SL)", "max_positions": 3, "max_entry_slip_bp": None},
     "First live test. Market entries swept thin testnet books (SOL fills up to 11% off); P&L later re-booked from Bybit."),
    ("Run 2 · v1 · explore · IOC entries, managed exits", ts("2026-11-10 13:55"), None,
     {**base, "exit_mode": "exchange until 14:12 UTC, then managed", "max_positions": 10, "max_entry_slip_bp": 10},
     "Exact fill booking, IOC-capped entries; managed exits (resting TP, watched stop, mark backstop) from 14:12."),
])
for r in db.query("SELECT r.id, r.name, (SELECT COUNT(*) FROM decisions d WHERE d.run_id=r.id) dec, "
                  "(SELECT COUNT(*) FROM trades t WHERE t.run_id=r.id) tr FROM runs r"):
    print(r)
