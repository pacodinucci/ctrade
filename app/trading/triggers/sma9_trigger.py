from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal, Optional, Any
from datetime import datetime, timezone

import pandas as pd

from app.trading.trend_logic import get_candles_async
from app.broker.ctrader_market_data import (
    open_market_order,
    close_position,
    set_position_sl_tp,
    has_open_position,
    get_open_positions,
    get_current_price,
)
from app.trading.trailing import stream_trailing_be_internal

# ---------------------------------------------------------------------
# TIPOS
# ---------------------------------------------------------------------
Trend = Literal["bullish", "bearish", "neutral"]
Side = Literal["buy", "sell"]

DEFAULT_SL_POINTS = 100


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
@dataclass(frozen=True)
class Sma9TriggerConfig:
    instrument: str
    timeframe: str
    trend: Trend

    sma_len: int = 9
    candles_count: int = 200
    poll_seconds: float = 1.0

    volume: float = 100.0
    stop_points: Optional[float] = None
    take_profit: Optional[float] = None

    close_on_sma_cross: bool = True
    trail_enabled: bool = True

    entry_tolerance: float = 0.15


# ---------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------
@dataclass
class Sma9TriggerState:
    waiting_entry: bool = True
    position_id: Optional[int] = None
    side: Optional[Side] = None
    entry_price: Optional[float] = None
    last_candle_time: Optional[str] = None
    trail_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
def _ensure_df(df: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower(): c for c in df.columns}
    required = ["time", "open", "high", "low", "close"]
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {missing}")

    out = df.rename(
        columns={
            cols["time"]: "time",
            cols["open"]: "open",
            cols["high"]: "high",
            cols["low"]: "low",
            cols["close"]: "close",
        }
    ).copy()

    out["open"] = out["open"].astype(float)
    out["close"] = out["close"].astype(float)
    return out.sort_values("time").reset_index(drop=True)


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).mean()


def _point_size(instrument: str) -> float:
    inst = instrument.upper()
    if inst.endswith("JPY"):
        return 0.001
    if inst.startswith("XAU"):
        return 0.01
    return 0.00001


def _entry_signal(trend: Trend, o: float, c: float, sma: float) -> Optional[Side]:
    if trend == "bullish" and o < sma and c > sma:
        return "buy"
    if trend == "bearish" and o > sma and c < sma:
        return "sell"
    return None


def _exit_signal(side: Side, close_price: float, sma: float) -> bool:
    return close_price < sma if side == "buy" else close_price > sma


def _side_from_trade_side(trade_side: Optional[int]) -> Optional[Side]:
    if trade_side == 1:
        return "buy"
    if trade_side == 2:
        return "sell"
    return None


async def _get_single_open_position(symbol: str) -> Optional[dict[str, Any]]:
    symbol_u = symbol.upper()
    positions = await get_open_positions()
    matches = [p for p in positions if str(p.get("symbol") or "").upper() == symbol_u]

    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeError(f"Más de una posición abierta para {symbol_u}")

    return matches[0]


def _calc_sl_from_entry(side: Side, entry_price: float, sl_points: float, instrument: str) -> float:
    dist = sl_points * _point_size(instrument)
    return entry_price - dist if side == "buy" else entry_price + dist

def _entry_tolerance_abs(instrument: str, tol_xau: float) -> float:
    """
    tol_xau=0.15 significa 0.15 en XAUUSD (point_size=0.01).
    Convertimos a 'points' y luego al point_size del instrumento.
    """
    xau_point = 0.01
    points = tol_xau / xau_point  # 0.15 / 0.01 = 15 points
    return points * _point_size(instrument)


def _entry_signal(trend: Trend, o: float, c: float, sma: float, tol_abs: float) -> Optional[Side]:
    """
    Con tolerancia:
    - bullish: o puede estar hasta sma + tol (cerca o incluso levemente arriba),
      pero c debe cerrar arriba "de verdad" => c > sma + tol
    - bearish: o puede estar hasta sma - tol,
      pero c debe cerrar abajo "de verdad" => c < sma - tol
    """
    if trend == "bullish":
        if o <= sma + tol_abs and c >= sma + tol_abs:
            return "buy"
    elif trend == "bearish":
        if o >= sma - tol_abs and c <= sma - tol_abs:
            return "sell"
    return None


# ---------------------------------------------------------------------
# MAIN TRIGGER
# ---------------------------------------------------------------------
async def run_sma9_trigger(cfg: Sma9TriggerConfig) -> None:
    if cfg.trend == "neutral":
        print("[SMA9 Trigger] Trend neutral → no se ejecuta")
        return

    state = Sma9TriggerState()

    print(
        f"[SMA9 Trigger] START instrument={cfg.instrument} tf={cfg.timeframe} "
        f"trend={cfg.trend} sma={cfg.sma_len}"
    )

    while True:
        # ---------------------------------------------------------
        # 0) Adoptar posición existente (si existe)
        # ---------------------------------------------------------
        if state.position_id is None and await has_open_position(cfg.instrument, side=None):
            pos = await _get_single_open_position(cfg.instrument)
            if pos:
                state.position_id = int(pos["position_id"])
                state.entry_price = float(pos["open_price"])
                state.side = _side_from_trade_side(pos.get("trade_side"))
                state.waiting_entry = False

                print(
                    f"[SMA9 Trigger] ⚠ Posición existente adoptada "
                    f"id={state.position_id} side={state.side} entry={state.entry_price}"
                )

        # ---------------------------------------------------------
        # Candles
        # ---------------------------------------------------------
        raw = await get_candles_async(cfg.instrument, cfg.timeframe, cfg.candles_count)
        df = _ensure_df(raw)

        last_time = str(df["time"].iloc[-1])
        if last_time == state.last_candle_time:
            await asyncio.sleep(cfg.poll_seconds)
            continue
        state.last_candle_time = last_time

        sma_last = float(_sma(df["close"], cfg.sma_len).iloc[-1])
        o_last = float(df["open"].iloc[-1])
        c_last = float(df["close"].iloc[-1])

        # ---------------------------------------------------------
        # 1) ENTRADA
        # ---------------------------------------------------------
        if state.waiting_entry and state.position_id is None:
            tol_abs = _entry_tolerance_abs(cfg.instrument, cfg.entry_tolerance)
            side = _entry_signal(cfg.trend, o_last, c_last, sma_last, tol_abs)

            print(f"[SMA9 Trigger] tol_abs={tol_abs}")
            print(
                f"[SMA9 Trigger] candle={last_time} O={o_last} C={c_last} "
                f"SMA={sma_last} entry={side}"
            )

            if side:
                await open_market_order(cfg.instrument, side, cfg.volume)

                pos = await _get_single_open_position(cfg.instrument)
                if not pos or pos.get("open_price") is None:
                    raise RuntimeError("No se pudo reconciliar la posición")

                state.position_id = int(pos["position_id"])
                state.entry_price = float(pos["open_price"])
                state.side = _side_from_trade_side(pos.get("trade_side")) or side
                state.waiting_entry = False

                print(
                    f"[SMA9 Trigger] OPEN side={state.side} "
                    f"entry={state.entry_price} id={state.position_id}"
                )

                # SL broker
                sl_points = cfg.stop_points or DEFAULT_SL_POINTS
                sl = _calc_sl_from_entry(state.side, state.entry_price, sl_points, cfg.instrument)

                await set_position_sl_tp(
                    symbol=cfg.instrument,
                    position_id=state.position_id,
                    stop_loss=sl,
                    take_profit=cfg.take_profit,
                )

                print(f"[SMA9 Trigger] SL set at {sl}")

                # Trailing interno
                if cfg.trail_enabled:
                    trail_side = "long" if state.side == "buy" else "short"

                    async def _get_price():
                        return {
                            "price": await get_current_price(cfg.instrument),
                            "time": datetime.now(timezone.utc).isoformat(),
                        }

                    async def _on_close_signal(_: dict):
                        if state.position_id is None:
                            return
                        print("[SMA9 Trigger] TRAILING CLOSE")
                        await close_position(state.position_id)
                        state = Sma9TriggerState(waiting_entry=True)

                    state.trail_task = asyncio.create_task(
                        stream_trailing_be_internal(
                            instrument=cfg.instrument,
                            side=trail_side,
                            entry_price=state.entry_price,
                            units=cfg.volume,
                            get_price=_get_price,
                            on_close_signal=_on_close_signal,
                            poll_seconds=0.2,
                            arm_at_points=50,
                            trail_distance_points=50,
                            be_buffer_points=5,
                        )
                    )

            await asyncio.sleep(cfg.poll_seconds)
            continue

        # ---------------------------------------------------------
        # 2) SALIDA POR SMA9
        # ---------------------------------------------------------
        if cfg.close_on_sma_cross and state.position_id and state.side:
            should_exit = _exit_signal(state.side, c_last, sma_last)

            print(
                f"[SMA9 Trigger] candle={last_time} C={c_last} SMA={sma_last} exit={should_exit}"
            )

            if should_exit:
                await close_position(state.position_id)
                print(f"[SMA9 Trigger] CLOSE id={state.position_id}")

                if state.trail_task:
                    state.trail_task.cancel()
                    state.trail_task = None

                state = Sma9TriggerState(waiting_entry=True)

        await asyncio.sleep(cfg.poll_seconds)
