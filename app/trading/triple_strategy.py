# app/trading/triple_strategy.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from app.trading.two_trend_validation import validate_triple_trend, BiasType
from app.trading.trend_logic import get_candles

SideType = Literal["buy", "sell"]
TriggerType = Literal["john_wick", "engulfing"]

# parámetros globales para John Wick
MIN_JW_WICK_BODY_RATIO = 2.0     # mecha principal >= 2x cuerpo
MIN_JW_BODY_RANGE_RATIO = 0.15   # cuerpo >= 15% del rango total (anti-doji)
MAX_OPPOSITE_WICK_RATIO = 0.25   # mecha opuesta <= 25% de la mecha principal


@dataclass
class StrategyDecision:
    instrument: str
    bias: BiasType  # "long" | "short" | "none"
    trigger_side: Optional[SideType]
    trigger_type: Optional[TriggerType]
    should_trade: bool
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    slow_tf: str
    mid_tf: str
    fast_tf: str
    trigger_tf: str = "M15"


# -----------------------
# Helpers de precio
# -----------------------

def _get_point_value(instrument: str) -> float:
    """
    Tamaño de 1 punto de precio (el mismo criterio que usabas en OANDA):

    - Pares con JPY → 3 decimales → 1 punto = 0.001
    - Resto (EURUSD, GBPUSD, etc.) → 5 decimales → 1 punto = 0.00001

    Entonces:
      - 200 puntos = 0.0020 en EURUSD (20 pips)
      - 200 puntos = 0.200  en GBPJPY (20 pips)
    """
    if instrument.endswith("JPY"):
        return 0.001
    return 0.00001


def _calc_sl_tp(
    instrument: str,
    side: SideType,
    entry_price: float,
    sl_points: int = 200,
    tp_points: int = 100,
) -> tuple[float, float]:
    """
    Devuelve (stop_loss, take_profit) a distancia fija en puntos de mercado.
    """
    point = _get_point_value(instrument)
    sl_dist = sl_points * point
    tp_dist = tp_points * point

    if side == "buy":
        sl = entry_price - sl_dist
        tp = entry_price + tp_dist
    else:  # sell
        sl = entry_price + sl_dist
        tp = entry_price - tp_dist

    return sl, tp


# -----------------------
# Patrones de disparador
# -----------------------

def _calc_wicks(row: pd.Series) -> tuple[float, float, float]:
    o = row["open"]
    h = row["high"]
    l = row["low"]
    c = row["close"]

    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return body, upper, lower


def _is_bullish_john_wick(
    row: pd.Series,
    min_ratio: float = MIN_JW_WICK_BODY_RATIO,
    min_body_range_ratio: float = MIN_JW_BODY_RANGE_RATIO,
    max_opposite_ratio: float = MAX_OPPOSITE_WICK_RATIO,
) -> bool:
    """
    John Wick alcista:
    - vela verde
    - cuerpo NO doji (mínimo % del rango total)
    - mecha inferior grande vs cuerpo (>= min_ratio * cuerpo)
    - mecha superior pequeña (<= max_opposite_ratio * mecha inferior)
    """
    body, upper, lower = _calc_wicks(row)

    h = row["high"]
    l = row["low"]
    rango_total = h - l
    if rango_total <= 0:
        return False

    # filtro anti-doji: cuerpo mínimo vs rango total
    if body <= 0:
        return False
    body_ratio = body / rango_total
    if body_ratio < min_body_range_ratio:
        return False

    # vela verde
    if row["close"] <= row["open"]:
        return False

    # mecha inferior dominante vs cuerpo
    if lower < min_ratio * body:
        return False

    # mecha superior tiene que ser chica comparada con la inferior
    if lower <= 0:
        return False
    if upper > max_opposite_ratio * lower:
        return False

    return True


def _is_bearish_john_wick(
    row: pd.Series,
    min_ratio: float = MIN_JW_WICK_BODY_RATIO,
    min_body_range_ratio: float = MIN_JW_BODY_RANGE_RATIO,
    max_opposite_ratio: float = MAX_OPPOSITE_WICK_RATIO,
) -> bool:
    """
    John Wick bajista:
    - vela roja
    - cuerpo NO doji (mínimo % del rango total)
    - mecha superior grande vs cuerpo (>= min_ratio * cuerpo)
    - mecha inferior pequeña (<= max_opposite_ratio * mecha superior)
    """
    body, upper, lower = _calc_wicks(row)

    h = row["high"]
    l = row["low"]
    rango_total = h - l
    if rango_total <= 0:
        return False

    if body <= 0:
        return False
    body_ratio = body / rango_total
    if body_ratio < min_body_range_ratio:
        return False

    # vela roja
    if row["close"] >= row["open"]:
        return False

    # mecha superior dominante vs cuerpo
    if upper < min_ratio * body:
        return False

    # mecha inferior chica comparada con la superior
    if upper <= 0:
        return False
    if lower > max_opposite_ratio * upper:
        return False

    return True


def _is_bullish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    """
    Engulfing alcista simple:
    - vela previa roja
    - vela actual verde
    - cuerpo actual envuelve al cuerpo previo
    """
    if not (prev["close"] < prev["open"] and curr["close"] > curr["open"]):
        return False

    prev_low_body = min(prev["open"], prev["close"])
    prev_high_body = max(prev["open"], prev["close"])
    curr_low_body = min(curr["open"], curr["close"])
    curr_high_body = max(curr["open"], curr["close"])

    return curr_low_body <= prev_low_body and curr_high_body >= prev_high_body


def _is_bearish_engulfing(prev: pd.Series, curr: pd.Series) -> bool:
    """
    Engulfing bajista simple:
    - vela previa verde
    - vela actual roja
    - cuerpo actual envuelve al cuerpo previo
    """
    if not (prev["close"] > prev["open"] and curr["close"] < curr["open"]):
        return False

    prev_low_body = min(prev["open"], prev["close"])
    prev_high_body = max(prev["open"], prev["close"])
    curr_low_body = min(curr["open"], curr["close"])
    curr_high_body = max(curr["open"], curr["close"])

    return curr_low_body <= prev_low_body and curr_high_body >= prev_high_body


def _detect_m15_trigger(
    instrument: str,
    bias: BiasType,
    trigger_tf: str = "M15",
    candles_m15: int = 100,
) -> tuple[Optional[SideType], Optional[TriggerType], Optional[float]]:
    """
    Devuelve (side, trigger_type, entry_price) si hay disparador, sino todo None.

    - Usa SOLO velas cerradas de M15.
    - Trigger = John Wick o Engulfing en la dirección del bias.
    - Entrada = close de la vela disparadora.
    """
    if bias not in ("long", "short"):
        return None, None, None

    df = get_candles(instrument, trigger_tf, count=candles_m15)

    if df is None or len(df) < 3:
        return None, None, None

    # Últimas dos velas cerradas:
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    # Bias LONG → buscar señales alcistas
    if bias == "long":
        # 1) John Wick alcista en la última vela
        if _is_bullish_john_wick(curr):
            return "buy", "john_wick", float(curr["close"])

        # 2) Engulfing alcista (prev, curr)
        if _is_bullish_engulfing(prev, curr):
            return "buy", "engulfing", float(curr["close"])

        return None, None, None

    # Bias SHORT → señales bajistas
    if bias == "short":
        if _is_bearish_john_wick(curr):
            return "sell", "john_wick", float(curr["close"])

        if _is_bearish_engulfing(prev, curr):
            return "sell", "engulfing", float(curr["close"])

        return None, None, None

    return None, None, None


# -----------------------
# Estrategia completa
# -----------------------

def evaluate_triple_tf_strategy_with_trigger(
    instrument: str,
    slow_tf: str = "H4",
    mid_tf: str = "H1",
    fast_tf: str = "M30",
    trigger_tf: str = "M15",
    sl_points: int = 200,
    tp_points: int = 100,
) -> StrategyDecision:
    """
    Estrategia:
      1) Validación triple de tendencia con Heiken Ashi:
         - slow_tf, mid_tf, fast_tf (por defecto H4, H1, M30).
      2) Si están alineados → bias long/short.
      3) En M15 buscamos:
           - John Wick
           - Engulfing
         a favor del bias.
      4) Si hay disparador:
           - entrada = close de la vela disparadora
           - SL = 200 puntos
           - TP = 100 puntos
    """
    triple = validate_triple_trend(
        instrument,
        slow_tf=slow_tf,
        mid_tf=mid_tf,
        fast_tf=fast_tf,
    )

    # Si no hay bias, no hay trade
    if triple.bias not in ("long", "short") or not triple.aligned:
        return StrategyDecision(
            instrument=instrument,
            bias=triple.bias,
            trigger_side=None,
            trigger_type=None,
            should_trade=False,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            slow_tf=slow_tf,
            mid_tf=mid_tf,
            fast_tf=fast_tf,
            trigger_tf=trigger_tf,
        )

    # Buscar disparador en M15
    side, trigger_type, entry_price = _detect_m15_trigger(
        instrument,
        bias=triple.bias,
        trigger_tf=trigger_tf,
    )

    if side is None or trigger_type is None or entry_price is None:
        return StrategyDecision(
            instrument=instrument,
            bias=triple.bias,
            trigger_side=None,
            trigger_type=None,
            should_trade=False,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            slow_tf=slow_tf,
            mid_tf=mid_tf,
            fast_tf=fast_tf,
            trigger_tf=trigger_tf,
        )

    # Calcular SL / TP
    sl, tp = _calc_sl_tp(
        instrument=instrument,
        side=side,
        entry_price=entry_price,
        sl_points=sl_points,
        tp_points=tp_points,
    )

    return StrategyDecision(
        instrument=instrument,
        bias=triple.bias,
        trigger_side=side,
        trigger_type=trigger_type,
        should_trade=True,
        entry_price=entry_price,
        stop_loss=sl,
        take_profit=tp,
        slow_tf=slow_tf,
        mid_tf=mid_tf,
        fast_tf=fast_tf,
        trigger_tf=trigger_tf,
    )
