# app/trading/m5_ma_trigger.py
from __future__ import annotations

from typing import Literal
import pandas as pd
import pandas_ta as ta

from app.trading.trend_logic import TrendType

SignalType = Literal[
    "none",
    "long_entry",
    "long_exit",
    "short_entry",
    "short_exit",
]

MA_FAST = 9
MA_FILTER_1 = 50
MA_FILTER_2 = 100


def _ensure_mas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula EMA9, EMA50 y EMA100 sobre el close.
    Asume df con columnas: 'close', ordenado por tiempo ascendente.
    """
    df = df.copy()

    if "EMA_9" not in df.columns:
        df["EMA_9"] = ta.ema(df["close"], length=MA_FAST)

    if "EMA_50" not in df.columns:
        df["EMA_50"] = ta.ema(df["close"], length=MA_FILTER_1)

    if "EMA_100" not in df.columns:
        df["EMA_100"] = ta.ema(df["close"], length=MA_FILTER_2)

    return df


def get_m5_ma_signal(df_m5: pd.DataFrame, d1_trend: TrendType) -> SignalType:
    """
    Recibe velas M5 + tendencia diaria.

    Devuelve la señal para la última vela cerrada:
      - 'long_entry' / 'long_exit'
      - 'short_entry' / 'short_exit'
      - 'none'

    Reglas:

    1) Tendencia D1:
       - bullish → solo largos
       - bearish → solo cortos
       - neutral / no_data → none

    2) Confirmación M5:
       - bullish: EMA50 > EMA100
       - bearish: EMA50 < EMA100

    3) Trigger M5 con EMA9:
       - bullish:
           entrada: close cruza EMA9 hacia arriba
                    Y además EMA9 está por encima de EMA50 y EMA100
           salida: close cruza EMA9 hacia abajo
       - bearish:
           entrada: close cruza EMA9 hacia abajo
                    Y además EMA9 está por debajo de EMA50 y EMA100
           salida: close cruza EMA9 hacia arriba
    """
    if d1_trend not in ("bullish", "bearish"):
        return "none"

    if len(df_m5) < 120:
        return "none"

    df_m5 = _ensure_mas(df_m5)

    last = df_m5.iloc[-1]
    prev = df_m5.iloc[-2]

    c_last = float(last["close"])
    c_prev = float(prev["close"])

    ema9_last = float(last["EMA_9"])
    ema9_prev = float(prev["EMA_9"])

    ema50_last = float(last["EMA_50"])
    ema100_last = float(last["EMA_100"])

    bullish_ok = ema50_last > ema100_last
    bearish_ok = ema50_last < ema100_last

    # filtros adicionales: posición de la 9 respecto de 50 y 100
    ema9_above_filters = ema9_last > ema50_last and ema9_last > ema100_last
    ema9_below_filters = ema9_last < ema50_last and ema9_last < ema100_last

    # --------- lado alcista ---------
    if d1_trend == "bullish" and bullish_ok:
        # entrada: cruce alcista de close vs EMA9
        #          + EMA9 por encima de EMA50 y EMA100
        if (
            ema9_above_filters
            and c_last > ema9_last
            and c_prev <= ema9_prev
        ):
            return "long_entry"

        # salida: cruce bajista de close vs EMA9 (sin filtro extra)
        if c_last < ema9_last and c_prev >= ema9_prev:
            return "long_exit"

    # --------- lado bajista ---------
    if d1_trend == "bearish" and bearish_ok:
        # entrada: cruce bajista de close vs EMA9
        #          + EMA9 por debajo de EMA50 y EMA100
        if (
            ema9_below_filters
            and c_last < ema9_last
            and c_prev >= ema9_prev
        ):
            return "short_entry"

        # salida: cruce alcista de close vs EMA9 (sin filtro extra)
        if c_last > ema9_last and c_prev <= ema9_prev:
            return "short_exit"

    return "none"
