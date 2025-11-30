# app/trading/bot_runner.py
from __future__ import annotations

import time
import threading
from typing import Literal, Optional

import pandas as pd

from app.trading.trend_logic import get_candles, add_indicators, get_trend
from app.trading.john_wicks_logic import classify_john_wicks, MIN_WICK_RATIO
from app.trading.orders import open_risked_market_order
from app.trading.trailing import stream_and_trail_position, Side as TrailingSide

TrendType = Literal["bullish", "bearish", "neutral", "no_data"]

DEFAULT_TREND_TF = "M30"
DEFAULT_TREND_COUNT = 200

DEFAULT_JW_TF = "M5"
DEFAULT_JW_COUNT = 200

DEFAULT_POLL_SECONDS = 15

# lado “bot”
BotSide = Literal["long", "short"]


class JohnWickBot:
    def __init__(
        self,
        bot_id: str,
        instrument: str,
        trend_tf: str = DEFAULT_TREND_TF,
        jw_tf: str = DEFAULT_JW_TF,
        trend_count: int = DEFAULT_TREND_COUNT,
        jw_count: int = DEFAULT_JW_COUNT,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
    ):
        self.bot_id = bot_id
        self.instrument = instrument

        # ✅ timeframes por instancia
        self.trend_tf = trend_tf
        self.jw_tf = jw_tf
        self.trend_count = trend_count
        self.jw_count = jw_count
        self.poll_seconds = poll_seconds

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_5m_time: Optional[pd.Timestamp] = None

    # ---------- helpers internos ----------

    def _get_trend(self) -> dict:
        df = get_candles(self.instrument, self.trend_tf, count=self.trend_count)
        df = add_indicators(df)

        trend = get_trend(df)
        last = df.iloc[-1]

        return {
            "instrument": self.instrument,
            "trend": trend,
            "last_time": last["time"],
            "last_close": float(last["close"]),
            "ema50": float(last["EMA_50"])
            if not pd.isna(last["EMA_50"])
            else None,
            "ha_open": float(last["HA_open"]),
            "ha_close": float(last["HA_close"]),
        }

    def _loop(self):
        print(
            f"=== Bot {self.bot_id} iniciado para {self.instrument} "
            f"(trend_tf={self.trend_tf}, jw_tf={self.jw_tf}) ==="
        )
        while not self._stop_event.is_set():
            try:
                trend_info = self._get_trend()
                trend: TrendType = trend_info["trend"]  # type: ignore

                df_jw = get_candles(self.instrument, self.jw_tf, count=self.jw_count)
                df_jw = classify_john_wicks(df_jw, min_wick_ratio=MIN_WICK_RATIO)
                df_jw = df_jw.sort_values("time")

                if df_jw.empty:
                    print(f"[{self.bot_id}] No hay velas {self.jw_tf}, reintentando...")
                    time.sleep(self.poll_seconds)
                    continue

                # detection de nuevas velas 5m
                if self._last_5m_time is None:
                    new_candles = df_jw.tail(1)
                else:
                    new_candles = df_jw[df_jw["time"] > self._last_5m_time]

                if new_candles.empty:
                    time.sleep(self.poll_seconds)
                    continue

                self._last_5m_time = new_candles["time"].max()

                for _, r in new_candles.iterrows():
                    if self._stop_event.is_set():
                        break

                    candle_time = r["time"]
                    direction = r["direction"]
                    jw_type = r["john_wick_type"]

                    print("\n==============================================")
                    print(
                        f"[{self.bot_id} REPORTE {self.jw_tf}] "
                        f"{self.instrument} - Vela cerrada en {candle_time}"
                    )
                    print(f"=== Tendencia {self.trend_tf} ===")
                    print(f" Instrumento: {self.instrument}")
                    print(f" Tendencia: {trend.upper()}")
                    print(f" Última vela {self.trend_tf}: {trend_info['last_time']}")
                    print(f" Cierre {self.trend_tf}: {trend_info['last_close']}")
                    print(f" EMA 50: {trend_info['ema50']}")
                    print(
                        f" HA O/C: {trend_info['ha_open']} / {trend_info['ha_close']}"
                    )

                    print(f"\n=== Última vela {self.jw_tf} ===")
                    print(
                        f" O:{r['open']} H:{r['high']} "
                        f"L:{r['low']} C:{r['close']}"
                    )
                    print(f" direction: {direction}")
                    print(f" john_wick_type: {jw_type}")
                    print(
                        f" upper_wick: {r['upper_wick']:.5f} "
                        f"lower_wick: {r['lower_wick']:.5f} "
                        f"rango_total: {r['rango_total']:.5f}"
                    )

                    action_msg = " Acción: NO haríamos nada."
                    side: BotSide | None = None

                    if trend == "bullish" and jw_type == "bullish":
                        action_msg = (
                            " Acción: *** ABRIMOS POSICIÓN LARGA *** "
                            "(tendencia BULLISH + John Wick BULLISH)."
                        )
                        side = "long"
                    elif trend == "bearish" and jw_type == "bearish":
                        action_msg = (
                            " Acción: *** ABRIMOS POSICIÓN CORTA *** "
                            "(tendencia BEARISH + John Wick BEARISH)."
                        )
                        side = "short"
                    elif trend not in ("bullish", "bearish"):
                        action_msg = (
                            " Acción: NO operamos porque no hay tendencia clara "
                            "(neutral/no_data)."
                        )

                    print("\n=== Decisión ===")
                    print(action_msg)
                    print("==============================================")

                    if side is not None:
                        # 🔥 Nuevo flujo:
                        # 1) abrimos operación con riesgo
                        # 2) lanzamos trailing asíncrono en otro thread

                        import asyncio

                        async def _open_and_trail():
                            order = await open_risked_market_order(
                                instrument=self.instrument,
                                side=side,  # "long" | "short"
                                comment=f"JohnWickBot {self.bot_id}",
                            )

                            if order is None:
                                print(
                                    f"[{self.bot_id}] Orden bloqueada "
                                    f"(ya había posición abierta en {self.instrument})."
                                )
                                return

                            position_id = order["position_id"]
                            entry_price = order["entry_price"]
                            volume = order["volume"]

                            trailing_side: TrailingSide = (
                                "buy" if side == "long" else "sell"
                            )

                            print(
                                f"[{self.bot_id}] Iniciando trailing en {self.instrument} "
                                f"side={trailing_side} pos={position_id} "
                                f"entry={entry_price:.5f} volume={volume:.2f}"
                            )

                            await stream_and_trail_position(
                                instrument=self.instrument,
                                side=trailing_side,
                                position_id=position_id,
                                entry_price=entry_price,
                                units=volume,
                                bot_id=self.bot_id,
                            )

                        # igual que antes: trailing en un thread aparte
                        threading.Thread(
                            target=lambda: asyncio.run(_open_and_trail()),
                            daemon=True,
                        ).start()

                time.sleep(self.poll_seconds)

            except Exception as e:
                print(f"\n[Bot {self.bot_id} ERROR en {self.instrument}] {e}")
                time.sleep(self.poll_seconds)

        print(f"=== Bot {self.bot_id} detenido para {self.instrument} ===")

    # ---------- API pública (igual que en el proyecto viejo) ----------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
