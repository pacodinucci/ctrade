# app/trading/checkpoints/ha_last_candle.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.trading.trend_logic import get_candles_async

Trend = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class HaLastCandleCheckpointResult:
    instrument: str
    timeframe: str
    trend: Trend
    ha_open: float
    ha_close: float
    candle_time: str  # string para no acoplar formatos (index tz-aware/naive)


def _ensure_ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower(): c for c in df.columns}
    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(
            f"Faltan columnas OHLC requeridas: {missing}. Columnas: {list(df.columns)}"
        )

    out = df.copy()
    out = out.rename(
        columns={
            cols["open"]: "open",
            cols["high"]: "high",
            cols["low"]: "low",
            cols["close"]: "close",
        }
    )
    return out


def compute_heiken_ashi(df: pd.DataFrame) -> pd.DataFrame:
    df = _ensure_ohlc_columns(df)

    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)

    ha_close = (o + h + l + c) / 4.0

    ha_open = pd.Series(index=df.index, dtype="float64")
    ha_open.iloc[0] = (o.iloc[0] + c.iloc[0]) / 2.0
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0

    ha_high = pd.concat([h, ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([l, ha_open, ha_close], axis=1).min(axis=1)

    out = df.copy()
    out["ha_open"] = ha_open
    out["ha_high"] = ha_high
    out["ha_low"] = ha_low
    out["ha_close"] = ha_close
    return out


def trend_from_last_closed_heiken_ashi(df: pd.DataFrame) -> tuple[Trend, float, float, str]:
    if df is None or len(df) < 2:
        raise ValueError("Necesito al menos 2 velas para evaluar 'última cerrada'.")

    ha = compute_heiken_ashi(df)
    last = ha.iloc[-1]

    ha_o = float(last["ha_open"])
    ha_c = float(last["ha_close"])

    if ha_c > ha_o:
        trend: Trend = "bullish"
    elif ha_c < ha_o:
        trend = "bearish"
    else:
        trend = "neutral"

    candle_time = str(ha.index[-1]) if hasattr(ha, "index") else "N/A"
    return trend, ha_o, ha_c, candle_time


async def ha_last_candle_checkpoint(
    instrument: str,
    timeframe: str,
    *,
    count: int = 200,
) -> HaLastCandleCheckpointResult:
    """
    Checkpoint 1:
    - Baja 'count' velas del (instrument, timeframe)
    - Calcula Heikin Ashi
    - Tendencia = color de la última vela HA cerrada (verde/roja)
    """
    df = await get_candles_async(instrument, timeframe, count)

    trend, ha_o, ha_c, candle_time = trend_from_last_closed_heiken_ashi(df)

    return HaLastCandleCheckpointResult(
        instrument=instrument,
        timeframe=timeframe,
        trend=trend,
        ha_open=ha_o,
        ha_close=ha_c,
        candle_time=candle_time,
    )
