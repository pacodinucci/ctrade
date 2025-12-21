# app/trading/trend_logic.py
from __future__ import annotations

from typing import Literal

import asyncio
import pandas as pd
import pandas_ta as ta
import numpy as np

from app.broker.ctrader_market_data import get_trendbars

TrendType = Literal["bullish", "bearish", "neutral", "no_data"]

# En el bot original usábamos esto como helper para la diaria
TIMEFRAME_D = "D1"
CANDLES_COUNT = 200  # tiene que ser > 50 para EMA50


async def get_candles_async(
    instrument: str,
    tf: str,
    count: int = 200,
) -> pd.DataFrame:
    """
    Versión ASYNC: usar dentro de código async (bots, endpoints, etc).
    """
    return await get_trendbars(instrument, tf, count)


def get_candles(instrument: str, tf: str, count: int = 200) -> pd.DataFrame:
    """
    Versión síncrona para scripts (como test_double_trend.py).

    Si hay un event loop corriendo (por ej. dentro de FastAPI async),
    NO uses esta función: usá `await get_candles_async(...)`.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No hay loop → podemos usar asyncio.run
        return asyncio.run(get_candles_async(instrument, tf, count))
    else:
        # Ya hay loop corriendo → evitar reventar el proceso
        raise RuntimeError(
            "get_candles() síncrono llamado dentro de un loop async. "
            "Usá await get_candles_async() en su lugar."
        )


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega:
    - EMA50  → para tendencia diaria
    - EMA20  → para retrocesos en H1
    - Heiken Ashi (open/close)
    """
    df = df.copy()

    # EMA 50 (D1 trend)
    df["EMA_50"] = ta.ema(df["close"], length=50)

    # EMA 20 (retrocesos H1)
    df["EMA_20"] = ta.ema(df["close"], length=20)

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

def get_trend_for_instrument_tf(
    instrument: str,
    timeframe: str,
    count: int = CANDLES_COUNT,
) -> dict:
    """
    Versión genérica: igual que get_daily_trend_for_instrument,
    pero permitiendo elegir el timeframe.
    """
    df = get_candles(instrument, timeframe, count)
    df = add_indicators(df)

    trend = get_trend(df)
    last = df.iloc[-1]

    return {
        "instrument": instrument,
        "timeframe": timeframe,
        "trend": trend,
        "last_time": last["time"],
        "last_close": float(last["close"]),
        "ema50": float(last["EMA_50"]) if not pd.isna(last["EMA_50"]) else None,
        "ha_open": float(last["HA_open"]),
        "ha_close": float(last["HA_close"]),
        "df": df,
    }

