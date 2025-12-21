from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional
import pandas as pd


@dataclass(frozen=True)
class SwingPoint:
    ts: pd.Timestamp           # timestamp del segundo candle "con color" del patrón (j)
    level: float               # precio del swing (high o low)
    i1: int                    # índice posicional (iloc) candle 1 (i) - primera vela con color
    i2: int                    # índice posicional (iloc) candle 2 (j) - segunda vela con color
    o1: float; h1: float; l1: float; c1: float
    o2: float; h2: float; l2: float; c2: float


def _is_doji(o: float, c: float, doji_points: float) -> bool:
    """
    doji_points está en UNIDADES DE PRECIO (no en "points" enteros).
    Ej: EURUSD 10 points => 10 * 0.00001 = 0.00010
    """
    return abs(c - o) <= doji_points


def _color(o: float, c: float, doji_points: float) -> Optional[str]:
    """
    Devuelve:
      - "green" si close > open y no es doji
      - "red"   si close < open y no es doji
      - None    si es doji (no define color)
    """
    if _is_doji(o, c, doji_points):
        return None
    if c > o:
        return "green"
    if c < o:
        return "red"
    return None


def _compress_close_levels(
    swings: List[SwingPoint],
    k: int,
    merge_threshold_points: float,
) -> List[SwingPoint]:
    """
    Toma todos los swings detectados (en orden cronológico ascendente)
    y devuelve hasta k swings finales, aplicando la regla:

    - Si hay 2 swings a distancia <= merge_threshold_points,
      se promedian y cuentan como 1.
    - Si se fusiona (y por ende se reduce el conteo),
      se sigue buscando más atrás para completar k.

    NOTA: merge_threshold_points está en UNIDADES DE PRECIO.
    """
    if k <= 0:
        return []

    src = list(reversed(swings))  # más reciente primero

    out: List[SwingPoint] = []
    i = 0
    while i < len(src) and len(out) < k:
        a = src[i]

        if i + 1 >= len(src):
            out.append(a)
            break

        b = src[i + 1]
        if abs(a.level - b.level) <= merge_threshold_points:
            avg_level = (a.level + b.level) / 2.0

            merged = SwingPoint(
                ts=a.ts,  # mantener el más reciente
                level=avg_level,
                i1=min(a.i1, b.i1),
                i2=max(a.i2, b.i2),
                # conservar OHLC del más reciente (a). El level ya representa el promedio.
                o1=a.o1, h1=a.h1, l1=a.l1, c1=a.c1,
                o2=a.o2, h2=a.h2, l2=a.l2, c2=a.c2,
            )
            out.append(merged)
            i += 2
        else:
            out.append(a)
            i += 1

    return list(reversed(out))


def find_last_5_highs_lows_two_candle(
    df_h4: pd.DataFrame,
    doji_points: float = 10.0,
    k: int = 5,
    merge_threshold_points: float = 30.0,
) -> Tuple[List[SwingPoint], List[SwingPoint]]:
    """
    Regla nueva:
    - Dojis NO se ignoran.
    - Dojis NO cuentan como color.
    - Un patrón es: vela con color (i) + (0..n dojis) + vela con color (j).
      HIGH si green -> red
      LOW  si red -> green
    - El nivel se toma como el extremo ENTRE TODAS las velas i..j inclusive:
      HIGH: max(high)
      LOW : min(low)

    doji_points y merge_threshold_points están en UNIDADES DE PRECIO.
    """

    required = {"open", "high", "low", "close"}
    if not required.issubset(df_h4.columns):
        raise ValueError(f"df_h4 debe tener columnas {sorted(required)}")

    df = df_h4.sort_index()

    highs: List[SwingPoint] = []
    lows: List[SwingPoint] = []

    n = len(df)
    if n < 2:
        return [], []

    i = 0
    while i < n - 1:
        r1 = df.iloc[i]
        o1, h1, l1, c1 = float(r1["open"]), float(r1["high"]), float(r1["low"]), float(r1["close"])
        col1 = _color(o1, c1, doji_points)

        # Si i es doji, no puede ser el "primer color" del patrón
        if col1 is None:
            i += 1
            continue

        # buscar la siguiente vela con color (saltando dojis)
        j = i + 1
        while j < n:
            r2 = df.iloc[j]
            o2, h2, l2, c2 = float(r2["open"]), float(r2["high"]), float(r2["low"]), float(r2["close"])
            col2 = _color(o2, c2, doji_points)
            if col2 is not None:
                break
            j += 1

        if j >= n:
            break  # no hay segunda vela con color

        # timestamp del segundo candle con color
        ts2 = df.index[j] if isinstance(df.index, pd.DatetimeIndex) else pd.Timestamp.utcnow()

        # extremos incluyendo dojis intermedios (rango i..j)
        window = df.iloc[i : j + 1]

        if col1 == "green" and col2 == "red":
            level = float(window["high"].max())
            highs.append(
                SwingPoint(
                    ts=ts2,
                    level=level,
                    i1=i,
                    i2=j,
                    o1=o1, h1=h1, l1=l1, c1=c1,
                    o2=o2, h2=h2, l2=l2, c2=c2,
                )
            )

        elif col1 == "red" and col2 == "green":
            level = float(window["low"].min())
            lows.append(
                SwingPoint(
                    ts=ts2,
                    level=level,
                    i1=i,
                    i2=j,
                    o1=o1, h1=h1, l1=l1, c1=c1,
                    o2=o2, h2=h2, l2=l2, c2=c2,
                )
            )

        # Avanzar: podés elegir i=j (no solapa) o i=i+1 (solapa).
        # Para que el patrón sea secuencial y no se re-use el mismo candle como inicio,
        # avanzamos al segundo candle con color.
        i = j

    highs_final = _compress_close_levels(highs, k=k, merge_threshold_points=merge_threshold_points)
    lows_final = _compress_close_levels(lows, k=k, merge_threshold_points=merge_threshold_points)

    return highs_final, lows_final
