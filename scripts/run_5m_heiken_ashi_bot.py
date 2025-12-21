# scripts/run_m5_heiken_ashi_bot.py
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bots.m5_heiken_ashi import M5HeikenAshiBot


async def main():
    bot = M5HeikenAshiBot(
        instrument="XAUUSD",
        volume=100,               # 0.01 lote para XAUUSD según tu mapeo
        poll_interval_seconds=5,  # chequea nueva vela M5 cada 5 segundos
    )

    try:
        await bot.start()
    except asyncio.CancelledError:
        # cuando se cancela el loop (por Ctrl+C), salimos prolijo
        await bot.stop()
        print("🛑 Bot M5 Heiken Ashi cancelado (asyncio.CancelledError)")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # sin traceback feo
        print("\n👋 Bot M5 Heiken Ashi detenido manualmente con Ctrl+C")
