# app/trading/m5_sma9.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

Trend = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class HeikenAshiTrendResult:
    trend: Trend
    ha_open: float
    ha_close: float


def _ensure_ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Espera columnas: open, high, low, close (case-insensitive).
    Devuelve un df con esas columnas en minúscula.
    """
    cols = {c.lower(): c for c in df.columns}
    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(f"Faltan columnas OHLC requeridas: {missing}. Columnas: {list(df.columns)}")

    out = df.copy()
    out = out.rename(columns={cols["open"]: "open", cols["high"]: "high", cols["low"]: "low", cols["close"]: "close"})
    return out


def compute_heiken_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula Heikin Ashi a partir de OHLC.
    Retorna df con columnas: ha_open, ha_high, ha_low, ha_close.
    """
    df = _ensure_ohlc_columns(df)

    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)

    ha_close = (o + h + l + c) / 4.0

    ha_open = pd.Series(index=df.index, dtype="float64")
    # inicial: (open + close) / 2
    ha_open.iloc[0] = (o.iloc[0] + c.iloc[0]) / 2.0

    # recursivo: (ha_open_prev + ha_close_prev) / 2
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


def heiken_ashi_trend_from_last_closed(df: pd.DataFrame) -> HeikenAshiTrendResult:
    """
    Define tendencia SOLO con la última vela cerrada Heikin Ashi:
    - Verde: ha_close > ha_open => bullish
    - Roja:  ha_close < ha_open => bearish
    - Igual: neutral
    """
    if df is None or len(df) < 2:
        raise ValueError("Necesito al menos 2 velas para hablar de 'última cerrada' con seguridad.")

    ha = compute_heiken_ashi(df)

    last = ha.iloc[-1]  # asumimos que el caller trae velas cerradas
    ha_o = float(last["ha_open"])
    ha_c = float(last["ha_close"])

    if ha_c > ha_o:
        trend: Trend = "bullish"
    elif ha_c < ha_o:
        trend = "bearish"
    else:
        trend = "neutral"

    return HeikenAshiTrendResult(trend=trend, ha_open=ha_o, ha_close=ha_c)
