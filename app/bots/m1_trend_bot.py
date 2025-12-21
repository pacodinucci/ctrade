# app/bots/m1_trend_bot.py

from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Optional, Literal
from datetime import datetime, timezone

import pandas as pd

from app.broker.ctrader import get_account_balance
from app.broker.ctrader_market_data import open_market_order, set_position_sl_tp, has_open_position
from app.trading.two_trend_validation import validate_double_trend_async
from app.trading.triple_strategy import (
    _is_bullish_john_wick,
    _is_bearish_john_wick,
    _is_bullish_engulfing,
    _is_bearish_engulfing,
)
from app.trading.trend_logic import get_candles_async

# Reutilizamos la misma configuración de riesgo que el bot M15
from app.bots.m15_trend_bot import SYMBOL_RISK_CONFIG

Side = Literal["buy", "sell"]


@dataclass
class M1TrendBot:
    instrument: str
    running: bool = False
    last_m1_time: Optional[datetime] = None  # hora de la última vela M1 cerrada

    # ------------------------------
    # CONTROL DEL BOT
    # ------------------------------
    async def start(self):
        self.running = True
        print(f"🚀 Bot M1 iniciado para {self.instrument} (trend M15/M5, trigger M1)")

        while self.running:
            try:
                await self._check_new_m1_candle()
            except Exception as e:
                print(f"❌ Error en loop principal del bot: {e!r}")

            # Para M1, con chequear cada ~10 segundos alcanza
            await asyncio.sleep(10)

    async def stop(self):
        self.running = False
        print(f"🛑 Bot {self.instrument} detenido.")

    # ------------------------------
    # DETECCIÓN DE NUEVA VELA M1
    # ------------------------------
    async def _check_new_m1_candle(self):
        df = await get_candles_async(self.instrument, "M1", count=5)
        if df is None or df.empty:
            print("⚠️ get_candles_async(M1) devolvió vacío")
            return

        last_row = df.iloc[-1]
        last_time: datetime = last_row["time"]
        if not isinstance(last_time, datetime):
            last_time = pd.to_datetime(last_time)

        if self.last_m1_time is None:
            self.last_m1_time = last_time
            print(f"⏱ Primera referencia M1 → {last_time}")
            return

        if last_time <= self.last_m1_time:
            # No hay vela nueva
            return

        print(f"\n🟦 Nueva vela M1 cerrada en {last_time} → evaluando señal…")
        self.last_m1_time = last_time

        await self.on_new_m1_candle(df)

    # ------------------------------
    # LÓGICA PRINCIPAL EN NUEVA VELA M1
    # ------------------------------
    async def on_new_m1_candle(self, df_m1: Optional[pd.DataFrame] = None):
        # 0) No abrir más de una posición a la vez para este instrumento
        if await has_open_position(self.instrument):
            print(
                f"⛔ Ya existe al menos una posición abierta en {self.instrument}. "
                f"El bot M1 NO abrirá otra hasta que se cierre."
            )
            return
        
        # 1) Doble validación (M15, M5)
        double = await validate_double_trend_async(
            instrument=self.instrument,
            slow_tf="M15",  # tendencia lenta
            fast_tf="M5",   # tendencia rápida
        )

        if not double.aligned:
            print("❌ Doble tendencia (M15/M5) NO alineada → no operamos.")
            return

        bias = double.bias
        print(f"📌 Bias detectado (M15/M5): {bias}")

        # 2) Trigger en M1
        if df_m1 is None:
            df_m1 = await get_candles_async(self.instrument, "M1", count=200)

        if df_m1 is None or len(df_m1) < 2:
            print("⚠️ No hay suficientes velas M1 para trigger")
            return

        prev = df_m1.iloc[-2]
        curr = df_m1.iloc[-1]

        # --- LOG OHLC DE LA VELA M1 CERRADA ---
        curr_time = curr["time"]
        if not isinstance(curr_time, datetime):
            curr_time = pd.to_datetime(curr_time)

        o = float(curr["open"])
        h = float(curr["high"])
        l = float(curr["low"])
        c = float(curr["close"])

        base_ohlc_log = (
            f"[M1] {curr_time} "
            f"O={o:.5f} H={h:.5f} L={l:.5f} C={c:.5f}"
        )
        # --------------------------------------

        side, trigger_name = self._detect_trigger(prev, curr, bias)

        if side is None:
            print(f"{base_ohlc_log} → sin trigger (bias={bias})")
            print("❌ No hubo trigger JohnWick/Engulfing en M1 → no operamos.")
            return

        # Con trigger → log OHLC + tipo de trigger
        print(
            f"{base_ohlc_log} → TRIGGER {trigger_name} {side.upper()} (bias={bias})"
        )

        # Precio teórico de entrada (close de la vela M1)
        candle_close_price = c
        print(
            f"🟢 Trigger {trigger_name} detectado → "
            f"{side.upper()} @ close M1 {candle_close_price}"
        )

        # 3) Volumen según esquema de riesgo
        volume = await self._calc_volume_for_1pct(candle_close_price)
        print(
            f"📦 Volumen dinámico para 1% riesgo (100 pts): {volume} "
            f"({self.instrument}, side={side})"
        )

        # 4) Ejecutar trade (entry real lo devuelve la API, si hay ExecutionEvent)
        resp = await open_market_order(
            symbol=self.instrument,
            side=side,  # "buy" o "sell"
            volume=volume,
        )

        print("📈 ORDEN ABIERTA (respuesta raw):", resp)

        position_id = resp.get("position_id")
        api_entry_price = resp.get("entry_price")

        # Si la API no confirma posición, cortamos acá
        if position_id is None or api_entry_price is None:
            print(
                f"❌ No se pudo confirmar apertura en cTrader para {self.instrument}. "
                f"position_id={position_id}, entry_price={api_entry_price}. "
                f"Respuesta completa: {resp}"
            )
            return

        effective_entry = float(api_entry_price)
        print(f"✅ Precio de entrada real desde API: {effective_entry}")

        # 5) SL / TP calculados DESDE el precio de entrada real
        sl, tp = await self._calc_sl_tp(effective_entry, side)
        print(
            f"🎯 Niveles calculados desde entry real → "
            f"SL={sl:.5f} | TP={tp:.5f}"
        )
        # 6) Enviar SL/TP al broker
        try:
            update_resp = await set_position_sl_tp(
                symbol=self.instrument,
                position_id=position_id,
                stop_loss=sl,
                take_profit=tp,
            )
            print(
                f"✅ SL/TP enviados a broker para position_id={position_id}: "
                f"{update_resp}"
            )
        except Exception as e:
            print(
                f"❌ Error al setear SL/TP en broker para position_id={position_id}: {e!r}"
            )


    # ------------------------------
    # TRIGGERS
    # ------------------------------
    def _detect_trigger(self, prev: pd.Series, curr: pd.Series, bias: str):
        if bias == "long":
            if _is_bullish_john_wick(curr):
                return "buy", "john_wick"
            if _is_bullish_engulfing(prev, curr):
                return "buy", "engulfing"
        elif bias == "short":
            if _is_bearish_john_wick(curr):
                return "sell", "john_wick"
            if _is_bearish_engulfing(prev, curr):
                return "sell", "engulfing"

        return None, None

    # ------------------------------
    # SL / TP dinámicos según balance
    # ------------------------------
    async def _calc_sl_tp(self, entry: float, side: str):
        balance = await get_account_balance()

        point = self._point_value()  # 0.00001 FX, 0.001 JPY, 0.01 metales

        if balance < 1_000:
            sl_points = 100   # SL a 100 puntos
            tp_points = 50    # TP a 50 puntos
            print(
                f"[RISK] {self.instrument} → balance bajo ({balance:.2f} < 1000). "
                f"Usando SL={sl_points} puntos, TP={tp_points} puntos."
            )
        else:
            sl_points = 200   # SL a 200 puntos
            tp_points = 100   # TP a 100 puntos
            print(
                f"[RISK] {self.instrument} → balance={balance:.2f} >= 1000. "
                f"Usando SL={sl_points} puntos, TP={tp_points} puntos."
            )

        sl_dist = sl_points * point
        tp_dist = tp_points * point

        if side == "buy":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:  # "sell"
            sl = entry + sl_dist
            tp = entry - tp_dist

        return sl, tp

    def _point_value(self) -> float:
        """
        Tamaño de 1 "punto" en precio para este instrumento.
          - Pares con JPY → 0.001
          - Metales (XAU, XAG, AUX, etc.) → 0.01
          - Resto FX (5 decimales) → 0.00001
        """
        symbol = self.instrument.upper()
        if symbol.endswith("JPY"):
            return 0.001
        if symbol.startswith(("XAU", "XAG", "AUX", "SILVER", "GOLD")):
            return 0.01
        return 0.00001

    # ------------------------------
    # VOLUMEN (igual lógica que el bot M15)
    # ------------------------------
    async def _calc_volume_for_1pct(self, entry: float) -> int:
        balance = await get_account_balance()
        symbol = self.instrument.upper()

        cfg = SYMBOL_RISK_CONFIG.get(symbol)

        if cfg is not None:
            min_volume = cfg["min_volume"]
            volume_step = cfg["volume_step"]
            base_balance = cfg["base_balance"]
            base_volume = cfg["base_volume"]
        else:
            # Fallback genérico
            if symbol.startswith(("XAU", "XAG", "AUX", "SILVER", "GOLD")):
                # Metal: lote mínimo 100
                min_volume = 100
                volume_step = 100
                base_balance = 1_000
                base_volume = 100
                kind = "METAL"
            else:
                # FX estándar: lote mínimo 100000
                min_volume = 100_000
                volume_step = 100_000
                base_balance = 1_000
                base_volume = 100_000
                kind = "FX"

            print(
                f"[RISK] {symbol} sin config específica, usando fallback {kind}: "
                f"min_volume={min_volume}, step={volume_step}, "
                f"base_balance={base_balance}, base_volume={base_volume}"
            )

        # Regla especial: metales → siempre 0.01 lote (min_volume), sin escalar
        if symbol in ("XAUUSD", "AUXUSD", "XAGUSD"):
            volume = min_volume
            print(
                f"[RISK] {symbol} → usando SIEMPRE volumen mínimo={volume} (0.01 lote). "
                f"Balance={balance:.2f} (riesgo <> 1%, a propósito)."
            )
            return volume

        # Resto (FX): lógica con balance >= / < 1000
        if balance < base_balance:
            volume = min_volume
            print(
                f"[RISK] {symbol} → balance bajo ({balance:.2f} < {base_balance}), "
                f"usando volumen mínimo={volume} (≈0.01 lote, riesgo > 1%)."
            )
            return volume

        raw_volume = base_volume * (balance / base_balance)

        steps = int(raw_volume // volume_step)
        volume = max(min_volume, steps * volume_step)

        if volume < min_volume:
            volume = min_volume

        print(
            f"[RISK] {symbol} → balance={balance:.2f}, "
            f"raw_volume={raw_volume:.2f}, step={volume_step}, volumen_final={volume}"
        )

        return volume
