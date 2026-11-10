"""Slow-moving market context: Fear & Greed, macro tape, crypto news headlines."""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .store import MarketStore

log = logging.getLogger("ctb.context")

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
}
MACRO_TICKERS = {"SPX_fut": "ES=F", "NDX_fut": "NQ=F", "DXY": "DX-Y.NYB", "Gold": "GC=F", "US10Y": "^TNX", "VIX": "^VIX"}

# Which headlines map to which perp. Market-wide words map to every symbol.
SYMBOL_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "ether", "eth"],
    "SOLUSDT": ["solana", "sol"],
    "XRPUSDT": ["xrp", "ripple"],
    "DOGEUSDT": ["dogecoin", "doge"],
    "BNBUSDT": ["bnb", "binance"],
    "ADAUSDT": ["cardano", "ada"],
}
MARKET_WIDE = ["crypto", "sec", "fed", "etf", "stablecoin", "tariff", "inflation", "cpi", "rate cut", "liquidat"]
UA = {"User-Agent": "Mozilla/5.0 (compatible; classifier-trading-bot/0.1)"}


def _strip(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text or ""))).strip()


def match_symbols(title: str, summary: str, symbols: list[str]) -> tuple[list[str], bool]:
    text = f" {title} {summary} ".lower()
    hits = []
    for s in symbols:
        for kw in SYMBOL_KEYWORDS.get(s, [s.replace("USDT", "").lower()]):
            if re.search(rf"\b{re.escape(kw)}\b", text):
                hits.append(s)
                break
    market_wide = any(kw in text for kw in MARKET_WIDE)
    return hits, market_wide


class ContextFeed:
    def __init__(self, store: MarketStore, session: aiohttp.ClientSession):
        self.store = store
        self.session = session
        self._seen: set[str] = set()
        self.on_news: list = []  # async callbacks(item)
        self._stop = asyncio.Event()

    async def _get(self, url: str, *, as_json: bool) -> Any:
        async with self.session.get(url, headers=UA, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            return await (r.json(content_type=None) if as_json else r.text())

    async def fear_greed(self) -> None:
        try:
            data = await self._get("https://api.alternative.me/fng/?limit=8", as_json=True)
            rows = data.get("data", [])
            if rows:
                self.store.context["fear_greed"] = {
                    "value": int(rows[0]["value"]),
                    "label": rows[0]["value_classification"],
                    "week_ago": int(rows[-1]["value"]),
                }
        except Exception as exc:  # noqa: BLE001
            self.store.record_error("fear_greed", str(exc))

    async def macro(self) -> None:
        out = {}
        for name, ticker in MACRO_TICKERS.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1h"
                data = await self._get(url, as_json=True)
                res = data["chart"]["result"][0]
                closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
                if len(closes) >= 2:
                    last = closes[-1]
                    d1 = closes[-8] if len(closes) >= 8 else closes[0]
                    out[name] = {"last": round(last, 3), "chg_8h_pct": round(100 * (last / d1 - 1), 3)}
            except Exception as exc:  # noqa: BLE001
                self.store.record_error(f"macro:{name}", str(exc))
        if out:
            self.store.context["macro"] = out

    async def news(self) -> None:
        fresh = []
        for source, url in RSS_FEEDS.items():
            try:
                xml = await self._get(url, as_json=False)
                root = ET.fromstring(xml.encode("utf-8", "ignore"))
                for item in root.iter("item"):
                    title = _strip(item.findtext("title") or "")
                    link = (item.findtext("link") or "").strip()
                    summary = _strip(item.findtext("description") or "")[:600]
                    pub = item.findtext("pubDate")
                    try:
                        published = email.utils.parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else None
                    except (TypeError, ValueError):
                        published = None
                    nid = hashlib.sha1(f"{source}|{link or title}".encode()).hexdigest()[:16]
                    if not title or nid in self._seen:
                        continue
                    self._seen.add(nid)
                    syms, wide = match_symbols(title, summary, self.store.symbols)
                    fresh.append({
                        "id": nid, "source": source, "title": title, "summary": summary, "link": link,
                        "published_at": published.isoformat().replace("+00:00", "Z") if published else None,
                        "published_ts": published.timestamp() if published else None,
                        "first_seen_ts": time.time(), "symbols": syms, "market_wide": wide,
                    })
            except Exception as exc:  # noqa: BLE001
                self.store.record_error(f"news:{source}", str(exc))
        if fresh:
            news = (fresh + self.store.context["news"])
            news.sort(key=lambda n: n.get("published_ts") or n["first_seen_ts"], reverse=True)
            self.store.context["news"] = news[:150]
            for item in fresh:
                for cb in self.on_news:
                    asyncio.create_task(cb(item))

    async def loop(self, first_run_callbacks: bool = False) -> None:
        # The first pull seeds the "seen" set without firing callbacks, so a restart does not
        # spend the Kairos budget re-forecasting yesterday's headlines.
        cbs, self.on_news = self.on_news, ([] if not first_run_callbacks else self.on_news)
        await asyncio.gather(self.fear_greed(), self.macro(), self.news())
        self.on_news = cbs
        tick = 0
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=90)
            except asyncio.TimeoutError:
                pass
            tick += 1
            await self.news()
            if tick % 10 == 0:
                await asyncio.gather(self.fear_greed(), self.macro())

    def stop(self) -> None:
        self._stop.set()
