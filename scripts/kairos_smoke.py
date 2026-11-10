"""One live Kairos request with a real market state; prints validation results (never the key).

    .venv/bin/python scripts/kairos_smoke.py SOLUSDT
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiohttp  # noqa: E402

from ctb.config import load_settings  # noqa: E402
from ctb.features import build_state  # noqa: E402
from ctb.kairos.client import KairosClient, KairosError  # noqa: E402
from ctb.kairos.parse import validate  # noqa: E402
from ctb.kairos.questions import build_questions  # noqa: E402
from ctb.market.bybit_public import BybitPublicFeed  # noqa: E402
from ctb.market.store import MarketStore  # noqa: E402
from ctb.policy import decide  # noqa: E402


async def main(symbol: str) -> None:
    s = load_settings()
    if not s.kairos_api_key:
        sys.exit("KAIROS_API_KEY is not set (add it to .env). Create a Kairos-group key at https://minara.cloud")
    syms = sorted({symbol, "BTCUSDT"})
    s.symbols = syms
    async with aiohttp.ClientSession() as session:
        store = MarketStore(syms)
        await BybitPublicFeed(store, session).warmup()
        state, meta = build_state(store, symbol, s)
        questions = build_questions(meta["sl_bp"], meta["tp_bp"], meta["horizon_min"])
        client = KairosClient(s.kairos_base_url, lambda: s.kairos_api_key, session, timeout_s=s.kairos_timeout_s)
        try:
            res = await client.ask(state, questions)
        except KairosError as exc:
            sys.exit(f"Kairos request failed: {exc} (status={exc.status}, request_id={exc.request_id})")
        parsed, issues = validate(res.answers, questions)
        print(f"latency {res.latency_ms:.0f} ms, attempts {res.attempts}, {res.request_bytes} bytes, usage {res.usage}, meta {res.metadata}")
        print("contract issues:", issues or "none")
        print(json.dumps(res.answers, indent=1))
        print("policy:", decide(parsed, meta, s, has_position=False).as_dict())


asyncio.run(main(sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"))
