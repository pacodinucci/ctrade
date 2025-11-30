# app/trading/john_wicks_logic.py
from __future__ import annotations

from typing import Literal
import pandas as pd

from app.trading.trend_logic import get_candles

DEFAULT_TF = "H1"
DEFAULT_COUNT = 200
MIN_WICK_RATIO = 0.65  # 65%

JohnWickType = Literal["none", "bullish", "bearish"]


def _classify_row_as_john_wick(
    row: pd.Series,
    min_wick_ratio: float,
) -> JohnWickType:
    """
    - bullish: cola abajo grande
    - bearish: cola arriba grande
    - none: no es JW
    """
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])

    rango_total = h - l
    if rango_total <= 0:
        return "none"

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    if c > o:  # verde
        if lower_wick >= min_wick_ratio * rango_total:
            return "bullish"

    if c < o:  # roja
        if upper_wick >= min_wick_ratio * rango_total:
            return "bearish"

    return "none"


def classify_john_wicks(
    df: pd.DataFrame,
    min_wick_ratio: float = MIN_WICK_RATIO,
) -> pd.DataFrame:
    """
    Agrega columnas:
    - rango_total
    - upper_wick
    - lower_wick
    - direction
    - john_wick_type
    """
    df = df.copy()

    df["rango_total"] = df["high"] - df["low"]

    body_max = df[["open", "close"]].max(axis=1)
    body_min = df[["open", "close"]].min(axis=1)

    df["upper_wick"] = df["high"] - body_max
    df["lower_wick"] = body_min - df["low"]

    def _direction(row: pd.Series) -> str:
        if row["close"] > row["open"]:
            return "bullish"
        elif row["close"] < row["open"]:
            return "bearish"
        return "doji"

    df["direction"] = df.apply(_direction, axis=1)

    df["john_wick_type"] = df.apply(
        _classify_row_as_john_wick,
        axis=1,
        min_wick_ratio=min_wick_ratio,
    )

    return df


def find_john_wicks_for_instrument(
    instrument: str,
    tf: str = DEFAULT_TF,
    count: int = DEFAULT_COUNT,
    min_wick_ratio: float = MIN_WICK_RATIO,
) -> pd.DataFrame:
    """
    Helper para debug manual.
    """
    df = get_candles(instrument, tf, count=count)
    df = classify_john_wicks(df, min_wick_ratio=min_wick_ratio)
    return df[df["john_wick_type"] != "none"].copy()
