# scripts/run_m5_ma_bot.py
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bots.m5_ma_trend_bot import M5MATrendBot


async def main():
    bot = M5MATrendBot(instrument="XAUUSD")
    try:
        await bot.start()
    except asyncio.CancelledError:
        await bot.stop()
        print("🛑 Bot M5 cancelado (asyncio.CancelledError)")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot M5 detenido manualmente con Ctrl+C")
