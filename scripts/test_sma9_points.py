# scripts/test_sma9_points.py
from __future__ import annotations

import sys
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, List, Tuple

import pandas as pd

# --- asegura import "app.*" cuando corrés desde /scripts ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.trend_logic import get_candles_async  # usa tu wrapper

Side = Literal["long", "short"]


def point_size(instrument: str) -> float:
    inst = instrument.upper()
    if inst.endswith("JPY"):
        return 0.001
    if inst.startswith("XAU"):
        return 0.01
    return 0.00001


def tf_minutes(tf: str) -> int:
    tf = tf.upper()
    if tf.startswith("M"):
        return int(tf[1:])
    if tf == "H1":
        return 60
    if tf == "H4":
        return 240
    if tf == "D1":
        return 1440
    raise ValueError(f"Timeframe no soportado acá: {tf}")


def ensure_df(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower(): c for c in df.columns}
    required = ["time", "open", "high", "low", "close"]
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {missing}. Columnas: {list(df.columns)}")

    out = df.copy().rename(
        columns={
            cols["time"]: "time",
            cols["open"]: "open",
            cols["high"]: "high",
            cols["low"]: "low",
            cols["close"]: "close",
        }
    )
    out["open"] = out["open"].astype(float)
    out["close"] = out["close"].astype(float)
    out["time"] = pd.to_datetime(out["time"], utc=True)
    return out.sort_values("time").reset_index(drop=True)


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).mean()


def is_closed_candle(candle_time: pd.Timestamp, tf_min: int) -> bool:
    now = datetime.now(timezone.utc)
    age_sec = (now - candle_time.to_pydatetime()).total_seconds()
    return age_sec >= tf_min * 60


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    points: float


def _aligned(s9: float, s50: float, s100: float, side: Side) -> bool:
    # Long: 9 arriba de 50 arriba de 100
    if side == "long":
        return (s9 > s50) and (s50 > s100)
    # Short: 9 abajo de 50 abajo de 100
    return (s9 < s50) and (s50 < s100)


def backtest_sma9_cross_points(
    df: pd.DataFrame,
    instrument: str,
    side: Side,
    sma_len: int = 9,
) -> Tuple[float, List[Trade]]:
    ps = point_size(instrument)

    df = df.copy()
    # mantenemos sma_len para la SMA "trigger" (por default 9)
    df["sma9"] = sma(df["close"], 9)
    df["sma50"] = sma(df["close"], 50)
    df["sma100"] = sma(df["close"], 100)

    trades: List[Trade] = []
    in_pos = False
    entry_price: Optional[float] = None
    entry_time: Optional[str] = None

    for i in range(len(df)):
        o = float(df.loc[i, "open"])
        c = float(df.loc[i, "close"])
        t = str(df.loc[i, "time"])

        s9 = df.loc[i, "sma9"]
        s50 = df.loc[i, "sma50"]
        s100 = df.loc[i, "sma100"]

        # si falta cualquier SMA, no podemos evaluar
        if pd.isna(s9) or pd.isna(s50) or pd.isna(s100):
            continue

        s9 = float(s9)
        s50 = float(s50)
        s100 = float(s100)

        if not in_pos:
            # ---------------------------------------------------
            # FILTRO: solo entra si SMAs están alineadas
            # ---------------------------------------------------
            if not _aligned(s9, s50, s100, side):
                continue

            # ---------------------------------------------------
            # ENTRADA: cruce sobre SMA9 (open un lado, close otro)
            # ---------------------------------------------------
            if side == "long":
                if o < s9 and c > s9:
                    in_pos = True
                    entry_price = c  # asumimos entrada al cierre de esa vela
                    entry_time = t
            else:  # short
                if o > s9 and c < s9:
                    in_pos = True
                    entry_price = c
                    entry_time = t

        else:
            # ---------------------------------------------------
            # SALIDA: cierra del otro lado de SMA9 (sin exigir alineación)
            # ---------------------------------------------------
            exit_now = (c < s9) if side == "long" else (c > s9)

            if exit_now:
                exit_price = c
                assert entry_price is not None and entry_time is not None

                pts = (exit_price - entry_price) / ps if side == "long" else (entry_price - exit_price) / ps

                trades.append(
                    Trade(
                        entry_time=entry_time,
                        exit_time=t,
                        entry_price=float(entry_price),
                        exit_price=float(exit_price),
                        points=float(pts),
                    )
                )

                in_pos = False
                entry_price = None
                entry_time = None

    total_points = sum(tr.points for tr in trades)
    return total_points, trades


async def main() -> None:
    # CLI:
    # uv run scripts/test_sma9_points.py XAUUSD short
    # uv run scripts/test_sma9_points.py EURUSD long
    if len(sys.argv) < 3:
        print("Uso: uv run scripts/test_sma9_points.py <INSTRUMENTO> <long|short> [TF] [COUNT]")
        print("Ej:  uv run scripts/test_sma9_points.py XAUUSD short M5 288")
        raise SystemExit(1)

    instrument = sys.argv[1].upper()
    side: Side = sys.argv[2].lower()  # type: ignore
    tf = (sys.argv[3].upper() if len(sys.argv) >= 4 else "M5")
    count = int(sys.argv[4]) if len(sys.argv) >= 5 else 288

    tf_min = tf_minutes(tf)

    # pedimos más para filtrar "en formación" y quedarnos con N cerradas
    raw = await get_candles_async(instrument, tf, count + 120)  # +120 por SMA100
    df = ensure_df(raw)

    # filtrar solo velas cerradas
    df = df[df["time"].apply(lambda x: is_closed_candle(x, tf_min))].reset_index(drop=True)

    if len(df) < count:
        raise RuntimeError(f"No hay suficientes velas cerradas: {len(df)} (< {count}). Pedí más histórico.")

    df = df.iloc[-count:].reset_index(drop=True)

    total, trades = backtest_sma9_cross_points(df, instrument, side)

    print("========================================")
    print("SMA9 cross backtest (with SMA9/50/100 alignment filter)")
    print(f"Instrument: {instrument} | Side: {side} | TF: {tf} | Candles(closed): {count}")
    print(f"Point size: {point_size(instrument)}")
    print("----------------------------------------")
    print(f"Trades: {len(trades)}")
    print(f"Total points: {total:.1f}")
    if trades:
        wins = sum(1 for t in trades if t.points > 0)
        losses = sum(1 for t in trades if t.points <= 0)
        avg = total / len(trades)
        print(f"Wins: {wins} | Losses: {losses} | Avg points/trade: {avg:.1f}")
        print("----------------------------------------")
        for tr in trades[-10:]:
            print(
                f"{tr.entry_time} -> {tr.exit_time} | "
                f"entry={tr.entry_price} exit={tr.exit_price} | pts={tr.points:.1f}"
            )
    print("========================================")


if __name__ == "__main__":
    asyncio.run(main())
