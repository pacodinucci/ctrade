# app/trading/levels.py
from __future__ import annotations

from typing import Literal, TypedDict, List, Tuple

import pandas as pd

LevelType = Literal["support", "resistance"]


class Level(TypedDict):
    type: LevelType
    price: float
    touches: int
    first_time: pd.Timestamp
    last_time: pd.Timestamp


def detect_levels(
    df: pd.DataFrame,
    tol_price: float,
    min_touches: int,
) -> Tuple[List[Level], List[Level]]:
    """
    A partir de un DF OHLC:
      - detecta swings (máximos/mínimos locales)
      - agrupa precios cercanos en clusters (tolerancia en precio)
      - devuelve listas de soportes y resistencias

    Parámetros:
      df: DataFrame con columnas ['open', 'high', 'low', 'close'] y un índice de tiempo
      tol_price: tolerancia en precio (NO en pips; ej EURUSD -> 0.0010, etc.)
      min_touches: toques mínimos para considerar válido un nivel
    """

    if df.empty:
        return [], []

    # --- 1) Swings: máximos/mínimos locales -----------------------------
    swing_high_mask = (df["high"] > df["high"].shift(1)) & (
        df["high"] > df["high"].shift(-1)
    )
    swing_low_mask = (df["low"] < df["low"].shift(1)) & (
        df["low"] < df["low"].shift(-1)
    )

    highs = df[swing_high_mask]["high"]
    lows = df[swing_low_mask]["low"]

    # --- 2) Clustering de niveles ---------------------------------------
    def _cluster_levels(series: pd.Series, tol: float, kind: LevelType) -> List[Level]:
        """
        Agrupa precios cercanos en clusters de nivel.
        kind: "resistance" o "support"
        """
        points = [(idx, float(price)) for idx, price in series.items()]
        if not points:
            return []

        # Ordenamos por precio (no por tiempo)
        points.sort(key=lambda x: x[1])

        clusters = []
        current_points = [points[0]]
        current_prices = [points[0][1]]

        for t, price in points[1:]:
            mean_price = sum(current_prices) / len(current_prices)
            if abs(price - mean_price) <= tol:
                current_points.append((t, price))
                current_prices.append(price)
            else:
                clusters.append((current_points, current_prices))
                current_points = [(t, price)]
                current_prices = [price]

        clusters.append((current_points, current_prices))

        levels: List[Level] = []
        for pts, prices in clusters:
            if len(pts) < min_touches:
                continue

            mean_price = sum(prices) / len(prices)
            first_time = pts[0][0]
            last_time = pts[-1][0]

            levels.append(
                Level(
                    type=kind,
                    price=mean_price,
                    touches=len(pts),
                    first_time=first_time,
                    last_time=last_time,
                )
            )

        return levels

    res_levels = _cluster_levels(highs, tol_price, "resistance")
    sup_levels = _cluster_levels(lows, tol_price, "support")

    return sup_levels, res_levels


def sort_levels_by_distance(
    levels: List[Level],
    ref_price: float,
) -> List[Level]:
    """
    Útil para debug o para elegir el nivel más relevante:
    ordena los niveles por distancia absoluta al precio de referencia.
    """
    return sorted(levels, key=lambda lvl: abs(lvl["price"] - ref_price))
