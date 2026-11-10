"""Pure-python technical indicators over plain float lists. No pandas dependency.

All functions return None when there is not enough data, never a misleading zero.
"""

from __future__ import annotations

import math
from typing import Sequence


def ema_series(values: Sequence[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(values: Sequence[float], period: int) -> float | None:
    s = ema_series(values, period)
    return s[-1] if s else None


def sma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def true_ranges(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return trs


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float | None:
    trs = true_ranges(highs, lows, closes)
    if len(trs) < period:
        return None
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float] | None:
    if len(closes) < slow + signal:
        return None
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    offset = len(ef) - len(es)
    line = [ef[i + offset] - es[i] for i in range(len(es))]
    sig = ema_series(line, signal)
    if not sig:
        return None
    return line[-1], sig[-1], line[-1] - sig[-1]


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> tuple[float, float, float] | None:
    """Returns (adx, +DI, -DI) using Wilder smoothing."""
    n = len(closes)
    if n < 2 * period + 1:
        return None
    pdm, mdm, trs = [], [], []
    for i in range(1, n):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    def wilder(xs: list[float]) -> list[float]:
        s = [sum(xs[:period])]
        for x in xs[period:]:
            s.append(s[-1] - s[-1] / period + x)
        return s

    str_, spdm, smdm = wilder(trs), wilder(pdm), wilder(mdm)
    dxs, pdi, mdi = [], 0.0, 0.0
    for t, p, m in zip(str_, spdm, smdm):
        if t == 0:
            dxs.append(0.0)
            continue
        pdi, mdi = 100 * p / t, 100 * m / t
        dxs.append(0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi))
    if len(dxs) < period:
        return None
    a = sum(dxs[:period]) / period
    for d in dxs[period:]:
        a = (a * (period - 1) + d) / period
    return a, pdi, mdi


def bollinger_width_pct(closes: Sequence[float], period: int = 20, k: float = 2.0) -> float | None:
    if len(closes) < period:
        return None
    w = closes[-period:]
    m = sum(w) / period
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / period)
    return 100 * (2 * k * sd) / m if m else None


def bollinger_pos(closes: Sequence[float], period: int = 20, k: float = 2.0) -> float | None:
    """Position of last close inside the band: 0 = lower band, 1 = upper band (can exceed)."""
    if len(closes) < period:
        return None
    w = closes[-period:]
    m = sum(w) / period
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / period)
    if sd == 0:
        return 0.5
    return (closes[-1] - (m - k * sd)) / (2 * k * sd)


def realized_vol_pct(closes: Sequence[float], period: int = 20) -> float | None:
    """Std of log returns over `period` bars, in percent per bar (not annualized)."""
    if len(closes) < period + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - period, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    return 100 * math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def pct_change(closes: Sequence[float], bars: int) -> float | None:
    if len(closes) <= bars or closes[-bars - 1] == 0:
        return None
    return 100 * (closes[-1] / closes[-bars - 1] - 1)


def range_position(highs: Sequence[float], lows: Sequence[float], close: float, bars: int) -> float | None:
    """Where close sits in the last `bars` high-low range: 0 = at the low, 1 = at the high."""
    if len(highs) < bars:
        return None
    hi, lo = max(highs[-bars:]), min(lows[-bars:])
    return 0.5 if hi == lo else (close - lo) / (hi - lo)


def vwap(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], vols: Sequence[float], bars: int) -> float | None:
    if len(closes) < bars:
        return None
    pv = sum((h + l + c) / 3 * v for h, l, c, v in zip(highs[-bars:], lows[-bars:], closes[-bars:], vols[-bars:]))
    vv = sum(vols[-bars:])
    return pv / vv if vv else None


def percentile_rank(history: Sequence[float], value: float) -> float | None:
    if not history:
        return None
    return 100 * sum(1 for x in history if x <= value) / len(history)
