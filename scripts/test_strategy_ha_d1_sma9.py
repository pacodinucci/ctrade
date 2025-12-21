from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.strategies.ha_d1_sma9_trigger import StrategyHaD1Sma9Config, run_strategy_ha_d1_sma9


async def main():
    cfg = StrategyHaD1Sma9Config(
        instrument="XAUUSD",
        checkpoint_timeframe="H4",
        trigger_timeframe="M5",
        checkpoint_count=200,
        trigger_count=200,
        trigger_poll_seconds=1.0,
        volume=100.0,
        stop_points=None,  # si querés: 200
        take_profit=None,
    )
    await run_strategy_ha_d1_sma9(cfg)


if __name__ == "__main__":
    asyncio.run(main())
