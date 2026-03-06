# app/trading/bias/d1_determinant_signal.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Direction = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class DailyCandle:
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class D1DeterminantSignal:
    """
    Señal determinante basada en D1, pero *sin estado*.
    No decide bias final; solo detecta el evento.
    """
    triggered: bool
    direction: Optional[Direction]           # "bullish" | "bearish" | None
    mid: float                               # low + 0.5*(high-low) del día actual
    broke_prev_high: bool                    # today.high > prev.high
    broke_prev_low: bool                     # today.low < prev.low
    conflict_outside_day: bool               # rompió ambos lados
    reason: str


def detect_d1_determinant_signal(
    prev: DailyCandle,
    today: DailyCandle,
) -> D1DeterminantSignal:
    """
    Reglas (repaso exacto, determinista):

    mid_today = today.low + 0.5*(today.high - today.low)

    - Bullish event:
        today.high > prev.high  AND  today.close >= mid_today

    - Bearish event (simétrico):
        today.low < prev.low    AND  today.close <= mid_today

    - conflict_outside_day:
        today.high > prev.high  AND  today.low < prev.low

    Si hay conflicto (outside day), la herramienta NO fuerza dirección;
    deja direction=None y triggered=False (o podés setear triggered=True pero
    con direction=None; aquí lo dejamos en False para evitar ambigüedad).
    """

    day_range = today.high - today.low
    if day_range <= 0:
        mid = today.low  # degenerado, pero consistente
        return D1DeterminantSignal(
            triggered=False,
            direction=None,
            mid=mid,
            broke_prev_high=False,
            broke_prev_low=False,
            conflict_outside_day=False,
            reason="invalid_daily_range: today.high <= today.low",
        )

    mid = today.low + 0.5 * day_range

    broke_prev_high = today.high > prev.high
    broke_prev_low = today.low < prev.low
    conflict = broke_prev_high and broke_prev_low

    # Si rompe ambos lados, es un outside day: no determinamos dirección acá.
    if conflict:
        return D1DeterminantSignal(
            triggered=False,
            direction=None,
            mid=mid,
            broke_prev_high=broke_prev_high,
            broke_prev_low=broke_prev_low,
            conflict_outside_day=True,
            reason=(
                "outside_day_conflict: broke_prev_high AND broke_prev_low; "
                f"prev.high={prev.high:.6g} prev.low={prev.low:.6g} | "
                f"today.high={today.high:.6g} today.low={today.low:.6g} "
                f"today.close={today.close:.6g} mid={mid:.6g}"
            ),
        )

    bullish = broke_prev_high and (today.close >= mid)
    bearish = broke_prev_low and (today.close <= mid)

    if bullish:
        return D1DeterminantSignal(
            triggered=True,
            direction="bullish",
            mid=mid,
            broke_prev_high=broke_prev_high,
            broke_prev_low=broke_prev_low,
            conflict_outside_day=False,
            reason=(
                "d1_determinant_bullish: today.high > prev.high AND today.close >= mid | "
                f"prev.high={prev.high:.6g} | today.high={today.high:.6g} "
                f"today.close={today.close:.6g} mid={mid:.6g}"
            ),
        )

    if bearish:
        return D1DeterminantSignal(
            triggered=True,
            direction="bearish",
            mid=mid,
            broke_prev_high=broke_prev_high,
            broke_prev_low=broke_prev_low,
            conflict_outside_day=False,
            reason=(
                "d1_determinant_bearish: today.low < prev.low AND today.close <= mid | "
                f"prev.low={prev.low:.6g} | today.low={today.low:.6g} "
                f"today.close={today.close:.6g} mid={mid:.6g}"
            ),
        )

    return D1DeterminantSignal(
        triggered=False,
        direction=None,
        mid=mid,
        broke_prev_high=broke_prev_high,
        broke_prev_low=broke_prev_low,
        conflict_outside_day=False,
        reason=(
            "no_d1_determinant: conditions not met | "
            f"broke_prev_high={broke_prev_high} broke_prev_low={broke_prev_low} "
            f"today.close={today.close:.6g} mid={mid:.6g}"
        ),
    )
