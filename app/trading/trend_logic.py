# app/trading/trend_logic.py
from __future__ import annotations

from typing import Literal

import pandas as pd
import pandas_ta as ta

TrendType = Literal["bullish", "bearish", "neutral", "no_data"]

# En el bot original usábamos esto como helper para la diaria
TIMEFRAME_D = "D"
CANDLES_COUNT = 200  # tiene que ser > 50 para EMA50


def get_candles(instrument: str, tf: str, count: int = 200) -> pd.DataFrame:
    """
    DESCARGA DE VELAS (PENDIENTE cTRADER)

    En el bot viejo esto llamaba a OANDA.
    Acá vamos a hacer exactamente lo mismo pero contra cTrader.

    Por ahora lo dejamos como NotImplemented para no inventar el endpoint.
    Cuando tengamos la app 'Active' y tokens listos:
      - implementamos aquí la llamada al Open API de cTrader
      - devolvemos un DataFrame con columnas: time, open, high, low, close
    """
    raise NotImplementedError("get_candles() aún no está implementado para cTrader")


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega EMA 50 y velas Heiken Ashi.

    Esto es prácticamente igual al bot anterior.
    """
    df = df.copy()

    # EMA 50 sobre el cierre
    df["EMA_50"] = ta.ema(df["close"], length=50)

    # Heiken Ashi
    ha = ta.ha(df["open"], df["high"], df["low"], df["close"])
    df["HA_open"] = ha["HA_open"]
    df["HA_close"] = ha["HA_close"]

    return df


def get_trend(df: pd.DataFrame) -> TrendType:
    """
    Devuelve:
    - 'bullish' si: precio > EMA 50 y vela Heiken Ashi verde
    - 'bearish' si: precio < EMA 50 y vela Heiken Ashi roja
    - 'neutral' si no cumple ninguna
    - 'no_data' si faltan datos (NaN)
    """
    if df.empty:
        return "no_data"

    last = df.iloc[-1]
    ema50 = last["EMA_50"]
    ha_open = last["HA_open"]
    ha_close = last["HA_close"]

    if pd.isna(ema50) or pd.isna(ha_open) or pd.isna(ha_close):
        return "no_data"

    price = last["close"]
    is_ha_green = ha_close > ha_open
    is_ha_red = ha_close < ha_open

    if price > ema50 and is_ha_green:
        return "bullish"
    elif price < ema50 and is_ha_red:
        return "bearish"
    else:
        return "neutral"


def get_daily_trend_for_instrument(instrument: str) -> dict:
    """
    Helper opcional: tendencia diaria completa para reusar en otros módulos.

    OJO: esto llama a get_candles(), que por ahora no está implementado,
    pero la firma y el flujo son los mismos que el bot anterior.
    """
    df = get_candles(instrument, TIMEFRAME_D, CANDLES_COUNT)
    df = add_indicators(df)

    trend = get_trend(df)
    last = df.iloc[-1]

    return {
        "instrument": instrument,
        "trend": trend,
        "last_time": last["time"],
        "last_close": float(last["close"]),
        "ema50": float(last["EMA_50"]) if not pd.isna(last["EMA_50"]) else None,
        "ha_open": float(last["HA_open"]),
        "ha_close": float(last["HA_close"]),
        "df_daily": df,
    }
