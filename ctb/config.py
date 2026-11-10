"""Runtime configuration. Everything is read from the environment (optionally a local .env).

Secrets (KAIROS_API_KEY, BYBIT_API_KEY, BYBIT_API_SECRET) are only ever read here and passed
to the clients that need them. They are never logged, stored in SQLite, or sent to the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def save_env(updates: dict[str, str], path: Path = ROOT / ".env") -> None:
    """Upsert KEY=value lines in .env (keeps comments/other keys), owner-only permissions."""
    for k, v in updates.items():
        if not k.replace("_", "").isalnum() or any(c in v for c in "\r\n"):
            raise ValueError(f"invalid value for {k}")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done: set[str] = set()
    out = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in updates and not line.lstrip().startswith("#"):
            if key not in done:
                out.append(f"{key}={updates[key]}")
                done.add(key)
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.environ.update(updates)


def key_hint(value: str) -> dict:
    """What the browser may see about a secret: whether it is set and its last 4 chars."""
    return {"set": bool(value), "hint": f"…{value[-4:]}" if len(value) >= 8 else ("set" if value else "")}


# Trading-timeframe profiles: horizon and stop sizing move together with the decision cadence,
# so switching timeframe never silently changes the strategy's holding period or risk geometry.
TF_PROFILES: dict[str, dict] = {
    "1": {"label": "1M", "horizon_bars": 30, "atr_tf": "5"},     # 30 min hold, 5m ATR stop
    "5": {"label": "5M", "horizon_bars": 24, "atr_tf": "15"},    # 2 h hold, 15m ATR stop
    "15": {"label": "15M", "horizon_bars": 16, "atr_tf": "60"},  # 4 h hold, 1h ATR stop
    "60": {"label": "1H", "horizon_bars": 12, "atr_tf": "240"},  # 12 h hold, 4h ATR stop
}


# Field defaults below read os.environ at import time, so .env must be loaded first.
load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    return [s.strip().upper() for s in os.environ.get(name, default).split(",") if s.strip()]


@dataclass
class Settings:
    # --- markets ---
    symbols: list[str] = field(default_factory=lambda: _list("CTB_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT"))
    decision_tf: str = os.environ.get("CTB_DECISION_TF", "5")  # Bybit interval string, minutes
    horizon_bars: int = _i("CTB_HORIZON_BARS", 24)  # bracket/time-stop horizon, in decision bars
    atr_tf: str = os.environ.get("CTB_ATR_TF", "15")  # timeframe whose ATR sizes the bracket

    # --- kairos ---
    kairos_base_url: str = os.environ.get("KAIROS_BASE_URL", "https://minara.cloud").rstrip("/")
    kairos_model: str = "DMIND/Kairos"
    kairos_timeout_s: float = _f("CTB_KAIROS_TIMEOUT_S", 120)
    kairos_max_requests_per_hour: int = _i("CTB_KAIROS_MAX_RPH", 120)
    kairos_news_enabled: bool = _b("CTB_KAIROS_NEWS", True)
    kairos_news_max_per_hour: int = _i("CTB_KAIROS_NEWS_MAX_RPH", 20)

    # --- bracket geometry (multiples of decision-timeframe ATR) ---
    sl_atr: float = _f("CTB_SL_ATR", 1.5)
    tp_atr: float = _f("CTB_TP_ATR", 2.5)
    min_sl_bp: float = _f("CTB_MIN_SL_BP", 40)  # floor so stops are never inside noise/costs
    max_sl_bp: float = _f("CTB_MAX_SL_BP", 300)

    # --- policy ---
    min_edge_r: float = _f("CTB_MIN_EDGE_R", 0.15)  # required EV in R units after costs
    min_setup_quality: float = _f("CTB_MIN_SETUP_QUALITY", 1.5)  # score scale 0..4
    taker_fee_bp: float = _f("CTB_TAKER_FEE_BP", 5.5)
    slippage_bp: float = _f("CTB_SLIPPAGE_BP", 2.0)
    allow_short: bool = _b("CTB_ALLOW_SHORT", True)
    # entries are IOC limits at most this far past the best testnet price; unfilled size is dropped
    max_entry_slip_bp: float = _f("CTB_MAX_ENTRY_SLIP_BP", 10)
    tpsl_trigger: str = os.environ.get("CTB_TPSL_TRIGGER", "LastPrice")  # LastPrice | MarkPrice | IndexPrice
    bar_settle_s: float = _f("CTB_BAR_SETTLE_S", 0.3)  # wait after a bar closes before building state
    trading_enabled: bool = _b("CTB_TRADING_ENABLED", False)  # start in observe-only
    # exploration: trade Kairos's preferred side even when the strict quality gates block it
    exploration_mode: bool = _b("CTB_EXPLORATION", False)
    explore_min_lean_r: float = _f("CTB_EXPLORE_MIN_LEAN_R", 0.05)  # |EV long - EV short| needed
    explore_risk_pct: float = _f("CTB_EXPLORE_RISK_PCT", 0.1)  # % equity risked per exploration trade

    # --- risk ---
    risk_per_trade_pct: float = _f("CTB_RISK_PER_TRADE_PCT", 0.5)
    max_leverage: int = _i("CTB_MAX_LEVERAGE", 3)
    max_positions: int = _i("CTB_MAX_POSITIONS", 3)
    max_notional_usdt: float = _f("CTB_MAX_NOTIONAL_USDT", 5000)
    daily_loss_limit_pct: float = _f("CTB_DAILY_LOSS_LIMIT_PCT", 3.0)
    max_venue_divergence_bp: float = _f("CTB_MAX_VENUE_DIVERGENCE_BP", 150)

    # --- bybit (testnet only, enforced in code) ---
    bybit_api_key: str = os.environ.get("BYBIT_API_KEY", "")
    bybit_api_secret: str = os.environ.get("BYBIT_API_SECRET", "")

    # --- server ---
    host: str = os.environ.get("CTB_HOST", "127.0.0.1")
    port: int = _i("CTB_PORT", 8800)
    db_path: Path = Path(os.environ.get("CTB_DB_PATH", str(ROOT / "data" / "ctb.db")))
    kill_file: Path = ROOT / "KILL"

    @property
    def kairos_api_key(self) -> str:
        # Exact variable name only, per the Kairos contract. Never substitute another key.
        return os.environ.get("KAIROS_API_KEY", "").strip()

    @property
    def round_trip_cost_bp(self) -> float:
        return 2 * (self.taker_fee_bp + self.slippage_bp)

    @property
    def decision_tf_minutes(self) -> int:
        return {"D": 1440, "W": 10080}.get(self.decision_tf, None) or int(self.decision_tf)

    def required_rph(self, tf: str | None = None, probe_rate: float = 0.05) -> int:
        """Kairos requests/hour a trading timeframe needs: one per closed bar per symbol, plus probes."""
        minutes = int(tf or self.decision_tf)
        return int(round(len(self.symbols) * (60 / minutes) * (1 + 3 * probe_rate)))

    def apply_tf_profile(self, tf: str) -> dict[str, str]:
        p = TF_PROFILES[tf]
        self.decision_tf, self.horizon_bars, self.atr_tf = tf, p["horizon_bars"], p["atr_tf"]
        return {"CTB_DECISION_TF": tf, "CTB_HORIZON_BARS": str(p["horizon_bars"]), "CTB_ATR_TF": p["atr_tf"]}

    def public_view(self) -> dict:
        """Settings safe to show in the dashboard (no secrets)."""
        return {
            "symbols": self.symbols,
            "decision_tf": self.decision_tf,
            "tf_profiles": {tf: {**p, "horizon_minutes": p["horizon_bars"] * int(tf), "required_rph": self.required_rph(tf)}
                            for tf, p in TF_PROFILES.items()},
            "decision_tf_minutes": self.decision_tf_minutes,
            "horizon_bars": self.horizon_bars,
            "atr_tf": self.atr_tf,
            "horizon_minutes": self.horizon_bars * self.decision_tf_minutes,
            "sl_atr": self.sl_atr,
            "tp_atr": self.tp_atr,
            "min_sl_bp": self.min_sl_bp,
            "max_sl_bp": self.max_sl_bp,
            "min_edge_r": self.min_edge_r,
            "min_setup_quality": self.min_setup_quality,
            "round_trip_cost_bp": self.round_trip_cost_bp,
            "max_entry_slip_bp": self.max_entry_slip_bp,
            "tpsl_trigger": self.tpsl_trigger,
            "allow_short": self.allow_short,
            "trading_enabled": self.trading_enabled,
            "exploration_mode": self.exploration_mode,
            "explore_min_lean_r": self.explore_min_lean_r,
            "explore_risk_pct": self.explore_risk_pct,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_leverage": self.max_leverage,
            "max_positions": self.max_positions,
            "max_notional_usdt": self.max_notional_usdt,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "kairos_base_url": self.kairos_base_url,
            "kairos_model": self.kairos_model,
            "kairos_key_configured": bool(self.kairos_api_key),
            "kairos_max_requests_per_hour": self.kairos_max_requests_per_hour,
            "bybit_keys_configured": bool(self.bybit_api_key and self.bybit_api_secret),
            "venue": "bybit-testnet",
        }


def load_settings() -> Settings:
    load_dotenv()
    return Settings()
