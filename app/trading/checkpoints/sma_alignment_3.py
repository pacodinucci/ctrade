# app/trading/checkpoints/sma_alignment_3.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.trading.trend_logic import get_candles_async

Trend = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class SmaAlignment3CheckpointResult:
    instrument: str
    timeframe: str
    fast_len: int
    mid_len: int
    slow_len: int
    trend: Trend
    sma_fast: float
    sma_mid: float
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


async def sma_alignment_3_checkpoint(
    instrument: str,
    timeframe: str,
    sma_a: int,
    sma_b: int,
    sma_c: int,
    *,
    count: int = 300,
) -> SmaAlignment3CheckpointResult:
    """
    Checkpoint SMA Alignment (3 SMAs):
    - Calcula SMA para 3 períodos.
    - Toma la última vela cerrada.
    - Bullish si SMA_fast > SMA_mid > SMA_slow
    - Bearish si SMA_fast < SMA_mid < SMA_slow
    - Neutral en caso contrario

    Nota: fast/mid/slow se determinan ordenando los períodos (menor = fast).
    """
    lens = [int(sma_a), int(sma_b), int(sma_c)]
    if len(set(lens)) != 3:
        raise ValueError(f"Los 3 períodos SMA deben ser distintos. Recibido: {lens}")

    fast_len, mid_len, slow_len = sorted(lens)

    df = await get_candles_async(instrument, timeframe, count)
    close = _ensure_close_column(df)

    if len(df) < slow_len:
        raise ValueError(
            f"No hay suficientes velas para SMA{slow_len}. "
            f"Tenés {len(df)}; mínimo {slow_len}. Subí count."
        )

    sma_fast_s = _sma(close, fast_len)
    sma_mid_s = _sma(close, mid_len)
    sma_slow_s = _sma(close, slow_len)

    sma_fast = float(sma_fast_s.iloc[-1])
    sma_mid = float(sma_mid_s.iloc[-1])
    sma_slow = float(sma_slow_s.iloc[-1])

    if pd.isna(sma_fast) or pd.isna(sma_mid) or pd.isna(sma_slow):
        raise ValueError("Alguna SMA dio NaN en la última vela. Subí count o verificá datos.")

    if sma_fast > sma_mid > sma_slow:
        trend: Trend = "bullish"
    elif sma_fast < sma_mid < sma_slow:
        trend = "bearish"
    else:
        trend = "neutral"

    candle_time = str(df.index[-1]) if hasattr(df, "index") else "N/A"

    return SmaAlignment3CheckpointResult(
        instrument=instrument,
        timeframe=timeframe,
        fast_len=fast_len,
        mid_len=mid_len,
        slow_len=slow_len,
        trend=trend,
        sma_fast=sma_fast,
        sma_mid=sma_mid,
        sma_slow=sma_slow,
        candle_time=candle_time,
    )
