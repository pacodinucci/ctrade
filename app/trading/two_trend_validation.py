# app/trading/two_trend_validation.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.trading.trend_logic import get_candles, get_candles_async

TrendColor = Literal["bullish", "bearish", "doji"]
TrendType = Literal["bullish", "bearish", "mixed", "no_data"]
BiasType = Literal["long", "short", "none"]


@dataclass
class TfTrendResult:
    timeframe: str
    trend: TrendType
    last_color: TrendColor | None
    current_color: TrendColor | None


@dataclass
class DoubleTrendValidation:
    instrument: str
    slow_tf: str
    fast_tf: str
    slow: TfTrendResult
    fast: TfTrendResult
    aligned: bool
    bias: BiasType  # long / short / none


@dataclass
class TripleTrendValidation:
    instrument: str
    slow_tf: str
    mid_tf: str
    fast_tf: str
    slow: TfTrendResult
    mid: TfTrendResult
    fast: TfTrendResult
    aligned: bool
    bias: BiasType  # long / short / none


# ---------------------------
# Helpers Heiken Ashi
# ---------------------------

def _build_heiken_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Espera columnas: open, high, low, close
    Devuelve mismas columnas + ha_open, ha_high, ha_low, ha_close
    """
    if df.empty:
        return df

    ha = df.copy()

    ha["ha_close"] = (ha["open"] + ha["high"] + ha["low"] + ha["close"]) / 4

    ha["ha_open"] = 0.0
    for i in range(len(ha)):
        if i == 0:
            ha.iat[i, ha.columns.get_loc("ha_open")] = (
                ha["open"].iloc[0] + ha["close"].iloc[0]
            ) / 2
        else:
            prev = i - 1
            ha.iat[i, ha.columns.get_loc("ha_open")] = (
                ha["ha_open"].iloc[prev] + ha["ha_close"].iloc[prev]
            ) / 2

    ha["ha_high"] = ha[["high", "ha_open", "ha_close"]].max(axis=1)
    ha["ha_low"] = ha[["low", "ha_open", "ha_close"]].min(axis=1)

    return ha


def _ha_color(row: pd.Series) -> TrendColor:
    if row["ha_close"] > row["ha_open"]:
        return "bullish"
    if row["ha_close"] < row["ha_open"]:
        return "bearish"
    return "doji"


# ---------------------------
# Helpers de TF - SYNC
# ---------------------------

def _get_tf_trend(instrument: str, tf: str, count: int = 100) -> TfTrendResult:
    """
    Versión SÍNCRONA: usa get_candles(), para scripts fuera de asyncio.
    """
    df = get_candles(instrument, tf, count=count)

    if df is None or len(df) < 2:
        return TfTrendResult(
            timeframe=tf,
            trend="no_data",
            last_color=None,
            current_color=None,
        )

    ha = _build_heiken_ashi(df)

    last_row = ha.iloc[-1]  # última cerrada
    last_color = _ha_color(last_row)
    current_color = last_color  # usamos la misma

    if last_color == "bullish":
        trend: TrendType = "bullish"
    elif last_color == "bearish":
        trend = "bearish"
    else:
        trend = "mixed"

    return TfTrendResult(
        timeframe=tf,
        trend=trend,
        last_color=last_color,
        current_color=current_color,
    )


# ---------------------------
# Doble validación H4 + H1 (SYNC)
# ---------------------------

def validate_double_trend(
    instrument: str,
    slow_tf: str = "H4",
    fast_tf: str = "H1",
    candles_slow: int = 100,
    candles_fast: int = 100,
) -> DoubleTrendValidation:
    """
    Versión SÍNCRONA, para scripts tipo test_double_trend.py
    """
    slow = _get_tf_trend(instrument, slow_tf, count=candles_slow)
    fast = _get_tf_trend(instrument, fast_tf, count=candles_fast)

    aligned = slow.trend in ("bullish", "bearish") and slow.trend == fast.trend

    if aligned and slow.trend == "bullish":
        bias: BiasType = "long"
    elif aligned and slow.trend == "bearish":
        bias = "short"
    else:
        bias = "none"

    return DoubleTrendValidation(
        instrument=instrument,
        slow_tf=slow_tf,
        fast_tf=fast_tf,
        slow=slow,
        fast=fast,
        aligned=aligned,
        bias=bias,
    )


# ---------------------------
# Versión TRIPLE SYNC (si la querés usar en scripts)
# ---------------------------

def validate_triple_trend(
    instrument: str,
    slow_tf: str = "H4",   # marco mayor
    mid_tf: str = "H1",    # intermedio
    fast_tf: str = "M30",  # más rápido
    candles_slow: int = 100,
    candles_mid: int = 100,
    candles_fast: int = 100,
) -> TripleTrendValidation:
    slow = _get_tf_trend(instrument, slow_tf, count=candles_slow)
    mid = _get_tf_trend(instrument, mid_tf, count=candles_mid)
    fast = _get_tf_trend(instrument, fast_tf, count=candles_fast)

    aligned = (
        slow.trend in ("bullish", "bearish")
        and slow.trend == mid.trend == fast.trend
    )

    if aligned and slow.trend == "bullish":
        bias: BiasType = "long"
    elif aligned and slow.trend == "bearish":
        bias = "short"
    else:
        bias = "none"

    return TripleTrendValidation(
        instrument=instrument,
        slow_tf=slow_tf,
        mid_tf=mid_tf,
        fast_tf=fast_tf,
        slow=slow,
        mid=mid,
        fast=fast,
        aligned=aligned,
        bias=bias,
    )


# ---------------------------
# VERSIONES ASYNC (para usar dentro de bots)
# ---------------------------

async def _get_tf_trend_async(
    instrument: str,
    tf: str,
    count: int = 100,
) -> TfTrendResult:
    """
    Versión ASYNC: usa get_candles_async(), para correr dentro de un loop asyncio.
    """
    df = await get_candles_async(instrument, tf, count=count)

    if df is None or len(df) < 2:
        return TfTrendResult(
            timeframe=tf,
            trend="no_data",
            last_color=None,
            current_color=None,
        )

    ha = _build_heiken_ashi(df)

    last_row = ha.iloc[-1]  # última cerrada
    last_color = _ha_color(last_row)

    if last_color == "bullish":
        trend: TrendType = "bullish"
    elif last_color == "bearish":
        trend = "bearish"
    else:
        trend = "mixed"

    return TfTrendResult(
        timeframe=tf,
        trend=trend,
        last_color=last_color,
        current_color=None,
    )


async def validate_double_trend_async(
    instrument: str,
    slow_tf: str = "H4",
    fast_tf: str = "H1",
    candles_slow: int = 200,
    candles_fast: int = 200,
) -> DoubleTrendValidation:
    """
    Versión ASYNC para el bot M15.
    """
    slow = await _get_tf_trend_async(instrument, slow_tf, count=candles_slow)
    fast = await _get_tf_trend_async(instrument, fast_tf, count=candles_fast)

    aligned = slow.trend in ("bullish", "bearish") and slow.trend == fast.trend

    if aligned and slow.trend == "bullish":
        bias: BiasType = "long"
    elif aligned and slow.trend == "bearish":
        bias = "short"
    else:
        bias = "none"

    return DoubleTrendValidation(
        instrument=instrument,
        slow_tf=slow_tf,
        fast_tf=fast_tf,
        slow=slow,
        fast=fast,
        aligned=aligned,
        bias=bias,
    )


async def validate_triple_trend_async(
    instrument: str,
    slow_tf: str = "H4",
    mid_tf: str = "H1",
    fast_tf: str = "M30",
    candles_slow: int = 200,
    candles_mid: int = 200,
    candles_fast: int = 200,
) -> TripleTrendValidation:
    """
    Versión ASYNC por si más adelante volvés a la triple validación en el bot.
    """
    slow = await _get_tf_trend_async(instrument, slow_tf, count=candles_slow)
    mid = await _get_tf_trend_async(instrument, mid_tf, count=candles_mid)
    fast = await _get_tf_trend_async(instrument, fast_tf, count=candles_fast)

    aligned = (
        slow.trend in ("bullish", "bearish")
        and slow.trend == mid.trend == fast.trend
    )

    if aligned and slow.trend == "bullish":
        bias: BiasType = "long"
    elif aligned and slow.trend == "bearish":
        bias = "short"
    else:
        bias = "none"

    return TripleTrendValidation(
        instrument=instrument,
        slow_tf=slow_tf,
        mid_tf=mid_tf,
        fast_tf=fast_tf,
        slow=slow,
        mid=mid,
        fast=fast,
        aligned=aligned,
        bias=bias,
    )
