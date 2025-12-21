# app/bots/m5_ma_trend_bot.py

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Literal
from datetime import datetime, timezone

import pandas as pd
import pandas_ta as ta

from app.broker.ctrader import get_account_balance
from app.broker.ctrader_market_data import (
    open_market_order,
    get_open_positions,
    close_position,
)
from app.trading.two_trend_validation import validate_double_trend_async
from app.trading.trend_logic import get_candles_async

Side = Literal["buy", "sell"]

SYMBOL_RISK_CONFIG = {
    "AUXUSD": {
        "min_volume": 100,
        "volume_step": 100,
        "base_balance": 1_000,
        "base_volume": 100,
    },
    "XAUUSD": {
        "min_volume": 100,
        "volume_step": 100,
        "base_balance": 1_000,
        "base_volume": 100,
    },
    "XAGUSD": {
        "min_volume": 100,
        "volume_step": 100,
        "base_balance": 1_000,
        "base_volume": 100,
    },
}


@dataclass
class M5MATrendBot:
    instrument: str
    running: bool = False
    last_m5_time: Optional[datetime] = None
    current_position_side: Optional[Side] = None  # "buy" / "sell" / None

    # ------------------------------
    # CONTROL DEL BOT
    # ------------------------------
    async def start(self):
        self.running = True
        print(f"🚀 Bot M5 MA iniciado para {self.instrument}")

        while self.running:
            try:
                await self._check_new_m5_candle()
            except Exception as e:
                print(f"❌ Error en loop principal del bot M5: {e!r}")

            now = datetime.now(timezone.utc)
            minute = now.minute
            sec = now.second

            # minutos restantes hasta el próximo cierre M5
            mins_to_next = (5 - (minute % 5)) % 5
            if mins_to_next == 0 and sec < 10:
                sleep_s = 5
            else:
                sleep_s = mins_to_next * 60 - sec + 2

            await asyncio.sleep(max(5, sleep_s))

    async def stop(self):
        self.running = False
        print(f"🛑 Bot M5 {self.instrument} detenido.")

    # ------------------------------
    # DETECCIÓN DE NUEVA VELA M5
    # ------------------------------
    async def _check_new_m5_candle(self):
        df = await get_candles_async(self.instrument, "M5", count=5)
        if df is None or df.empty:
            print("⚠️ get_candles_async(M5) devolvió vacío")
            return

        last_row = df.iloc[-1]
        last_time = last_row["time"]
        if not isinstance(last_time, datetime):
            last_time = pd.to_datetime(last_time)

        if self.last_m5_time is None:
            self.last_m5_time = last_time
            print(f"⏱ Primera referencia M5 → {last_time}")
            return

        if last_time <= self.last_m5_time:
            return

        print(f"\n🟦 Nueva vela M5 cerrada en {last_time} → evaluando señal…")
        self.last_m5_time = last_time

        await self.on_new_m5_candle()

    # ------------------------------
    # LÓGICA PRINCIPAL EN NUEVA VELA
    # ------------------------------
    async def on_new_m5_candle(self):
        # 1) Doble validación (H4, H1) usando la MISMA lógica que el M15
        double = await validate_double_trend_async(
            instrument=self.instrument,
            slow_tf="H4",
            fast_tf="H1",
        )

        if not double.aligned:
            print("❌ Doble tendencia (H4/H1) NO alineada → no operamos.")
            return

        bias = double.bias  # "long" o "short"
        print(f"📌 Bias detectado (H4/H1): {bias}")

        # 2) Velas M5 para filtros y trigger
        df_m5 = await get_candles_async(self.instrument, "M5", count=200)
        if df_m5 is None or len(df_m5) < 50:
            print("⚠️ No hay suficientes velas M5 para filtros y trigger")
            return

        last = df_m5.iloc[-1]

        curr_time = last["time"]
        if not isinstance(curr_time, datetime):
            curr_time = pd.to_datetime(curr_time)

        o = float(last["open"])
        h = float(last["high"])
        l = float(last["low"])
        c = float(last["close"])

        # Calculamos MAs en M5
        df_m5 = df_m5.copy()
        df_m5["EMA_50"] = ta.ema(df_m5["close"], length=50)
        df_m5["EMA_100"] = ta.ema(df_m5["close"], length=100)
        df_m5["EMA_9"] = ta.ema(df_m5["close"], length=9)

        last = df_m5.iloc[-1]
        ema50 = float(last["EMA_50"])
        ema100 = float(last["EMA_100"])
        ema9 = float(last["EMA_9"])

        bull_filter = ema50 > ema100
        bear_filter = ema50 < ema100

        base_ohlc_log = (
            f"[M5] {curr_time} "
            f"O={o:.5f} H={h:.5f} L={l:.5f} C={c:.5f}"
        )

        print(
            f"{base_ohlc_log} → EMA50={ema50:.5f}, EMA100={ema100:.5f}, "
            f"bull_filter={bull_filter}, bear_filter={bear_filter}, EMA9={ema9:.5f}, "
            f"pos_side={self.current_position_side}"
        )

        # ------------------------------
        # REGLAS EXACTAS:
        # - bias long  → solo largos
        # - bias short → solo cortos
        # - filtro M5:
        #     long: EMA50 > EMA100
        #     short: EMA50 < EMA100
        # - trigger con EMA9:
        #     long:
        #        entrada: vela cierra por encima de EMA9
        #        salida:  vela cierra por debajo de EMA9
        #     short:
        #        entrada: vela cierra por debajo de EMA9
        #        salida:  vela cierra por encima de EMA9
        # ------------------------------
        if bias == "long":
            if not bull_filter:
                print(
                    f"🔴 Filtro M5 NO válido para largos (EMA50 <= EMA100) → no operamos."
                )
                return

            # Entrada long
            if self.current_position_side is None and c > ema9:
                print(
                    f"🟢 TRIGGER LONG → close ({c:.5f}) > EMA9 ({ema9:.5f}). "
                    f"Abrimos BUY."
                )
                await self._open_position("buy")
                return

            # Salida long
            if self.current_position_side == "buy" and c < ema9:
                print(
                    f"🟠 TRIGGER SALIDA LONG → close ({c:.5f}) < EMA9 ({ema9:.5f}). "
                    f"Cerramos BUY."
                )
                await self._close_positions_for_side("buy")
                return

            print("ℹ️ EMA9 sin trigger en LONG, mantenemos estado actual.")
            return

        if bias == "short":
            if not bear_filter:
                print(
                    f"🔴 Filtro M5 NO válido para cortos (EMA50 >= EMA100) → no operamos."
                )
                return

            # Entrada short
            if self.current_position_side is None and c < ema9:
                print(
                    f"🟢 TRIGGER SHORT → close ({c:.5f}) < EMA9 ({ema9:.5f}). "
                    f"Abrimos SELL."
                )
                await self._open_position("sell")
                return

            # Salida short
            if self.current_position_side == "sell" and c > ema9:
                print(
                    f"🟠 TRIGGER SALIDA SHORT → close ({c:.5f}) > EMA9 ({ema9:.5f}). "
                    f"Cerramos SELL."
                )
                await self._close_positions_for_side("sell")
                return

            print("ℹ️ EMA9 sin trigger en SHORT, mantenemos estado actual.")
            return

        print(f"⚪ Bias desconocido ({bias}) → no operamos.")

    # ------------------------------
    # APERTURA / CIERRE DE POSICIONES
    # ------------------------------
    async def _open_position(self, side: Side):
        # Calcular volumen como en M15TrendBot (1% con excepciones)
        volume = await self._calc_volume_for_1pct()

        print(
            f"📦 Abriendo {side.upper()} en {self.instrument} con volumen={volume}…"
        )
        resp = await open_market_order(
            symbol=self.instrument,
            side=side,
            volume=volume,
        )
        print("📈 ORDEN ABIERTA (respuesta raw):", resp)

        self.current_position_side = side

    async def _close_positions_for_side(self, side: Side):
        positions = await get_open_positions()
        symbol_u = self.instrument.upper()
        desired_trade_side = 1 if side == "buy" else 2  # 1=BUY, 2=SELL

        to_close = [
            p
            for p in positions
            if str(p.get("symbol") or "").upper() == symbol_u
            and p.get("trade_side") == desired_trade_side
        ]

        if not to_close:
            print(
                f"⚠️ No se encontraron posiciones {side.upper()} abiertas en {symbol_u} para cerrar."
            )
            self.current_position_side = None
            return

        for p in to_close:
            pos_id = p.get("position_id")
            if pos_id is None:
                continue
            try:
                print(
                    f"📉 Cerrando posición {pos_id} ({side.upper()}) en {symbol_u}…"
                )
                await close_position(position_id=int(pos_id))
            except Exception as e:
                print(f"❌ Error cerrando posición {pos_id}: {e!r}")

        self.current_position_side = None

    # ------------------------------
    # RIESGO / VOLUMEN (igual estilo que M15TrendBot)
    # ------------------------------
    def _point_value(self) -> float:
        symbol = self.instrument.upper()
        if symbol.endswith("JPY"):
            return 0.001
        if symbol.startswith(("XAU", "XAG", "AUX", "SILVER", "GOLD")):
            return 0.01
        return 0.00001

    async def _calc_volume_for_1pct(self) -> int:
        balance = await get_account_balance()
        symbol = self.instrument.upper()

        cfg = SYMBOL_RISK_CONFIG.get(symbol)

        if cfg is not None:
            min_volume = cfg["min_volume"]
            volume_step = cfg["volume_step"]
            base_balance = cfg["base_balance"]
            base_volume = cfg["base_volume"]
        else:
            if symbol.startswith(("XAU", "XAG", "AUX", "SILVER", "GOLD")):
                min_volume = 100
                volume_step = 100
                base_balance = 1_000
                base_volume = 100
                kind = "METAL"
            else:
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

        # Regla especial: metales → siempre 0.01 lote
        if symbol in ("XAUUSD", "AUXUSD", "XAGUSD"):
            volume = min_volume
            print(
                f"[RISK] {symbol} → usando SIEMPRE volumen mínimo={volume} (0.01 lote). "
                f"Balance={balance:.2f}."
            )
            return volume

        if balance < base_balance:
            volume = min_volume
            print(
                f"[RISK] {symbol} → balance bajo ({balance:.2f} < {base_balance}), "
                f"usando volumen mínimo={volume}."
            )
            return volume

        raw_volume = base_volume * (balance / base_balance)
        steps = int(raw_volume // volume_step)
        volume = max(min_volume, steps * volume_step)

        print(
            f"[RISK] {symbol} → balance={balance:.2f}, "
            f"raw_volume={raw_volume:.2f}, step={volume_step}, volumen_final={volume}"
        )

        return volume
