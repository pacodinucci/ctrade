# scripts/test_m5_sma9_trend.py
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# --- bootstrap: agregar root del proyecto al sys.path ---
ROOT = Path(__file__).resolve().parents[1]  # .../ctrade-api
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.trend_logic import get_candles_async
from app.trading.m5_sma9 import heiken_ashi_trend_from_last_closed


async def main():
    instrument = "USDJPY"
    tf = "D1"
    count = 200

    df = await get_candles_async(instrument, tf, count)

    res = heiken_ashi_trend_from_last_closed(df)

    last_time = df.index[-1] if hasattr(df, "index") and len(df.index) else "N/A"

    print("========================================")
    print(f"Instrumento: {instrument} | TF: {tf} | Velas: {count}")
    print(f"Última vela (índice): {last_time}")
    print(f"Heikin Ashi última cerrada -> ha_open={res.ha_open:.5f} ha_close={res.ha_close:.5f}")
    print(f"TENDENCIA (solo HA última cerrada): {res.trend}")
    print("========================================")


if __name__ == "__main__":
    asyncio.run(main())
