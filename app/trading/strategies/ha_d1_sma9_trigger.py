# app/trading/strategies/ha_d1_sma9_trigger.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.trading.checkpoints.ha_last_candle import ha_last_candle_checkpoint
from app.trading.triggers.sma9_trigger import Sma9TriggerConfig, run_sma9_trigger


@dataclass(frozen=True)
class StrategyHaD1Sma9Config:
    instrument: str

    checkpoint_timeframe: str = "H4"
    checkpoint_count: int = 200

    trigger_timeframe: str = "M5"
    trigger_count: int = 200
    trigger_poll_seconds: float = 1.0

    volume: float = 100.0
    sma_len: int = 9

    stop_points: Optional[float] = None
    take_profit: Optional[float] = None


async def run_strategy_ha_d1_sma9(cfg: StrategyHaD1Sma9Config) -> None:
    cp = await ha_last_candle_checkpoint(
        instrument=cfg.instrument,
        timeframe=cfg.checkpoint_timeframe,
        count=cfg.checkpoint_count,
    )

    print("========================================")
    print("[Strategy] HA(D1) + SMA9 Trigger(M5)")
    print(f"[Strategy] Instrument: {cfg.instrument}")
    print(f"[Strategy] Checkpoint: tf={cfg.checkpoint_timeframe} candle={cp.candle_time} trend={cp.trend}")
    print("========================================")

    if cp.trend == "neutral":
        print("[Strategy] Trend neutral => no se ejecuta trigger.")
        return

    tcfg = Sma9TriggerConfig(
        instrument=cfg.instrument,
        timeframe=cfg.trigger_timeframe,
        trend=cp.trend,
        sma_len=cfg.sma_len,
        candles_count=cfg.trigger_count,
        poll_seconds=cfg.trigger_poll_seconds,
        volume=cfg.volume,
        stop_points=cfg.stop_points,
        take_profit=cfg.take_profit,
        close_on_sma_cross=True,
    )

    await run_sma9_trigger(tcfg)
