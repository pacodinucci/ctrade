# app/trading/john_wicks_logic.py
from __future__ import annotations

from typing import Literal
import pandas as pd

from app.trading.trend_logic import get_candles

DEFAULT_TF = "H1"
DEFAULT_COUNT = 200
MIN_WICK_RATIO = 0.65  # 65%
MIN_BODY_RATIO = 0.06  

JohnWickType = Literal["none", "bullish", "bearish"]


def _classify_row_as_john_wick(
    row: pd.Series,
    min_wick_ratio: float,
    min_body_ratio: float = MIN_BODY_RATIO,
) -> JohnWickType:
    """
    - bullish: cola abajo grande
    - bearish: cola arriba grande
    - none: no es JW (incluye dojis o casi dojis)
    """
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])

    rango_total = h - l
    if rango_total <= 0:
        return "none"

    body = abs(c - o)
    body_ratio = body / rango_total

    # Si el cuerpo es muy chico -> doji o casi doji -> no es John Wick
    if body_ratio < min_body_ratio:
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
    min_body_ratio: float = MIN_BODY_RATIO,
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
        min_body_ratio=min_body_ratio,
    )

    return df
