# app/trading/checkpoints/sma_cross.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.trading.trend_logic import get_candles_async

Trend = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class SmaCrossCheckpointResult:
    instrument: str
    timeframe: str
    fast_len: int
    slow_len: int
    trend: Trend
    sma_fast: float
    sma_slow: float
    candle_time: str


def _ensure_close_column(df: pd.DataFrame) -> pd.Series:
    cols = {c.lower(): c for c in df.columns}
    if "close" not in cols:
        raise ValueError(f"Falta columna 'close'. Columnas: {list(df.columns)}")
    return df[cols["close"]].astype(float)


def _sma(series: pd.Series, length: int) -> pd.Series:
    if length <= 0:
        raise ValueError(f"length inválido: {length}. Debe ser > 0.")
    return series.rolling(window=length, min_periods=length).mean()


async def sma_cross_checkpoint(
    instrument: str,
    timeframe: str,
    fast_len: int,
    slow_len: int,
    *,
    count: int = 200,
) -> SmaCrossCheckpointResult:
    """
    Checkpoint SMA Cross (parametrizable):
    - Calcula SMA(fast_len) y SMA(slow_len) sobre close.
    - Evalúa el último valor:
        SMA_fast > SMA_slow => bullish
        SMA_fast < SMA_slow => bearish
        Igual => neutral
    """
    if fast_len == slow_len:
        raise ValueError("fast_len y slow_len no pueden ser iguales.")
    if fast_len > slow_len:
        # Para que el significado "fast/slow" sea consistente
        fast_len, slow_len = slow_len, fast_len

    df = await get_candles_async(instrument, timeframe, count)
    close = _ensure_close_column(df)

    sma_fast_s = _sma(close, fast_len)
    sma_slow_s = _sma(close, slow_len)

    if len(df) < slow_len:
        raise ValueError(
            f"No hay suficientes velas para SMA{slow_len}. "
            f"Tenés {len(df)}; mínimo {slow_len}. Subí count."
        )

    sma_fast = float(sma_fast_s.iloc[-1])
    sma_slow = float(sma_slow_s.iloc[-1])

    if pd.isna(sma_fast) or pd.isna(sma_slow):
        raise ValueError(
            "Último valor SMA es NaN. Subí count o verificá el feed."
        )

    if sma_fast > sma_slow:
        trend: Trend = "bullish"
    elif sma_fast < sma_slow:
        trend = "bearish"
    else:
        trend = "neutral"

    candle_time = str(df.index[-1]) if hasattr(df, "index") else "N/A"

    return SmaCrossCheckpointResult(
        instrument=instrument,
        timeframe=timeframe,
        fast_len=fast_len,
        slow_len=slow_len,
        trend=trend,
        sma_fast=sma_fast,
        sma_slow=sma_slow,
        candle_time=candle_time,
    )
