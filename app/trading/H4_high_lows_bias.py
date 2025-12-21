from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Tuple
import statistics

TrendBias = Literal["bullish", "bearish", "neutral", "insufficient_data"]


@dataclass(frozen=True)
class TrendBiasResult:
    bias: TrendBias
    score: float
    highs_slope: float
    lows_slope: float
    highs_up: int
    highs_down: int
    lows_up: int
    lows_down: int
    reason: str


def _count_moves(levels: List[float], eps: float) -> Tuple[int, int]:
    ups = 0
    downs = 0
    for i in range(1, len(levels)):
        d = levels[i] - levels[i - 1]
        if d > eps:
            ups += 1
        elif d < -eps:
            downs += 1
    return ups, downs


def _theil_sen_slope(levels: List[float]) -> float:
    """
    Estimador robusto de pendiente: mediana de pendientes entre pares.
    X es el índice (0..n-1). Muy robusto ante 1-2 outliers.
    """
    n = len(levels)
    if n < 2:
        return 0.0
    slopes = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            slopes.append((levels[j] - levels[i]) / (j - i))
    return statistics.median(slopes) if slopes else 0.0


def determine_trend_bias_from_swings_flexible(
    highs: List,  # objetos con .level
    lows: List,   # objetos con .level
    eps: float,
    min_points: int = 4,
    slope_weight: float = 1.0,
    violation_weight: float = 0.35,
    threshold: float = 0.8,
) -> TrendBiasResult:
    """
    Bias flexible/robusto:
      - Usa pendiente robusta (Theil–Sen) en highs y lows.
      - Penaliza violaciones, pero no invalida por un único swing.
      - score > +threshold => bullish
        score < -threshold => bearish
        sino neutral

    eps: tolerancia en PRECIO (ej: 30 points * point_size)
    threshold: cuanto más alto, más exigente.
    """

    if len(highs) < min_points or len(lows) < min_points:
        return TrendBiasResult(
            bias="insufficient_data",
            score=0.0,
            highs_slope=0.0,
            lows_slope=0.0,
            highs_up=0, highs_down=0,
            lows_up=0, lows_down=0,
            reason=f"need >= {min_points} highs and lows; got highs={len(highs)} lows={len(lows)}",
        )

    high_levels = [h.level for h in highs][-min_points:]
    low_levels = [l.level for l in lows][-min_points:]

    # Pendientes robustas (precio por swing-step)
    hslope = _theil_sen_slope(high_levels)
    lslope = _theil_sen_slope(low_levels)

    hu, hd = _count_moves(high_levels, eps)
    lu, ld = _count_moves(low_levels, eps)

    comparisons = min_points - 1
    if comparisons <= 0:
        comparisons = 1

    # Normalizaciones:
    # - slope_score: pendiente medida en "eps por step"
    slope_score = ((hslope / eps) + (lslope / eps)) / 2.0  # promedio highs/lows

    # - violation_score: en bullish, las bajadas son violaciones; en bearish, las subidas son violaciones.
    #   Como todavía no sabemos bias, lo incluimos como "asimetría": ups - downs (normalizado)
    #   Positivo favorece bullish, negativo favorece bearish.
    move_balance_highs = (hu - hd) / comparisons
    move_balance_lows = (lu - ld) / comparisons
    move_score = (move_balance_highs + move_balance_lows) / 2.0

    # Score total (combinación)
    score = slope_weight * slope_score + violation_weight * move_score

    # Decisión
    if score >= threshold:
        bias: TrendBias = "bullish"
        reason = (
            f"score={score:.2f} >= {threshold} | "
            f"hslope={hslope:.6g}, lslope={lslope:.6g} | "
            f"moves(highs u/d={hu}/{hd}, lows u/d={lu}/{ld})"
        )
    elif score <= -threshold:
        bias = "bearish"
        reason = (
            f"score={score:.2f} <= -{threshold} | "
            f"hslope={hslope:.6g}, lslope={lslope:.6g} | "
            f"moves(highs u/d={hu}/{hd}, lows u/d={lu}/{ld})"
        )
    else:
        bias = "neutral"
        reason = (
            f"score={score:.2f} within (-{threshold}, +{threshold}) | "
            f"hslope={hslope:.6g}, lslope={lslope:.6g} | "
            f"moves(highs u/d={hu}/{hd}, lows u/d={lu}/{ld})"
        )

    return TrendBiasResult(
        bias=bias,
        score=score,
        highs_slope=hslope,
        lows_slope=lslope,
        highs_up=hu,
        highs_down=hd,
        lows_up=lu,
        lows_down=ld,
        reason=reason,
    )
