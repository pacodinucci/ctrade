# scripts/run_15m_bot.py
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bots.m15_trend_bot import M15TrendBot


async def main():
    bot = M15TrendBot(instrument="XAUUSD")
    try:
        await bot.start()
    except asyncio.CancelledError:
        # cuando se cancela el loop (por Ctrl+C), salimos prolijo
        await bot.stop()
        print("🛑 Bot M15 cancelado (asyncio.CancelledError)")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # sin traceback feo
        print("\n👋 Bot detenido manualmente con Ctrl+C")
