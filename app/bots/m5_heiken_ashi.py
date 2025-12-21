# app/bots/m5_heiken_ashi.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import numpy as np
import pandas as pd

from app.broker.ctrader_market_data import (
    get_trendbars,
    has_open_position,
    get_open_positions,
    open_market_order,
    close_position,
)

Side = Literal["buy", "sell"]
Color = Literal["green", "red", "neutral"]


# -------------------------------------------------------------------
# Heiken Ashi helpers
# -------------------------------------------------------------------


def _add_heiken_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Espera columnas: time, open, high, low, close.
    Añade: ha_open, ha_close, ha_high, ha_low, ha_color.
    """
    if df.empty:
        return df

    df = df.copy()

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    n = len(df)
    ha_open = np.zeros(n, dtype=float)
    ha_close = np.zeros(n, dtype=float)
    ha_high = np.zeros(n, dtype=float)
    ha_low = np.zeros(n, dtype=float)

    for i in range(n):
        # HA close: media de OHLC clásicos
        ha_close[i] = (o[i] + h[i] + l[i] + c[i]) / 4.0

        if i == 0:
            # Primera vela: HA open = media de open/close clásicos
            ha_open[i] = (o[i] + c[i]) / 2.0
        else:
            # Resto: media de open/close HA anteriores
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

        ha_high[i] = max(h[i], ha_open[i], ha_close[i])
        ha_low[i] = min(l[i], ha_open[i], ha_close[i])

    df["ha_open"] = ha_open
    df["ha_close"] = ha_close
    df["ha_high"] = ha_high
    df["ha_low"] = ha_low

    colors: list[Color] = []
    for op, cl in zip(ha_open, ha_close):
        if cl > op:
            colors.append("green")
        elif cl < op:
            colors.append("red")
        else:
            colors.append("neutral")
    df["ha_color"] = colors

    return df


def _last_color(df: pd.DataFrame) -> Color:
    if df.empty or "ha_color" not in df.columns:
        return "neutral"
    return df["ha_color"].iloc[-1]


def _prev_color(df: pd.DataFrame) -> Color:
    if len(df) < 2 or "ha_color" not in df.columns:
        return "neutral"
    return df["ha_color"].iloc[-2]


# -------------------------------------------------------------------
# Bot M5 Heiken Ashi (D1 + H1 + M5)
# -------------------------------------------------------------------


@dataclass
class M5HeikenAshiBot:
    """
    Estrategia:

      - Si D1 HA es verde, H1 HA es verde y M5 HA es verde
        y NO hay posición abierta para el instrumento → abrir BUY.

      - Si D1 HA es roja, H1 HA es roja y M5 HA es roja
        y NO hay posición abierta para el instrumento → abrir SELL.

      - Si la última vela M5 cerrada CAMBIÓ de color respecto a la anterior,
        se cierra cualquier posición abierta de ese símbolo.

    Restricción: una sola posición abierta por símbolo (se verifica con
    has_open_position / get_open_positions).
    """

    instrument: str
    volume: int  # volumen en unidades cTrader (ej: 100000 para 0.01 lot FX)
    poll_interval_seconds: float = 5.0

    # estado interno
    running: bool = False
    last_m5_time: Optional[datetime] = None

    async def start(self) -> None:
        self.running = True
        print(f"🚀 [M5-HA] Bot iniciado para {self.instrument}")

        while self.running:
            try:
                await self._tick()
            except Exception as exc:
                print(f"❌ [M5-HA][{self.instrument}] ERROR en tick: {exc!r}")
            await asyncio.sleep(self.poll_interval_seconds)

    async def stop(self) -> None:
        self.running = False
        print(f"⏹ [M5-HA] Bot detenido para {self.instrument}")

    # -------------------------------------------------------------------
    # Lógica por tick (se ejecuta cada poll_interval_seconds)
    # -------------------------------------------------------------------

    async def _tick(self) -> None:
        symbol = self.instrument.upper()

        # 1) Descargar velas
        d1_df = await get_trendbars(symbol, "D1", count=200)
        h1_df = await get_trendbars(symbol, "H1", count=200)
        m5_df = await get_trendbars(symbol, "M5", count=200)

        if d1_df is None or h1_df is None or m5_df is None:
            return
        if m5_df.empty or len(m5_df) < 2:
            return

        # 2) Detectar nueva vela M5 cerrada (usamos la columna 'time')
        last_row = m5_df.iloc[-1]
        last_time = last_row.get("time")

        if not isinstance(last_time, datetime):
            # Si por lo que sea viene otro tipo, no hacemos nada
            return

        # Si no hay nueva vela (misma hora o más antigua), salimos
        if self.last_m5_time is not None and last_time <= self.last_m5_time:
            return

        # A partir de aquí sabemos que hay NUEVA vela M5 cerrada
        self.last_m5_time = last_time

        # 3) Calcular Heiken Ashi
        d1_ha = _add_heiken_ashi(d1_df)
        h1_ha = _add_heiken_ashi(h1_df)
        m5_ha = _add_heiken_ashi(m5_df)

        d1_color = _last_color(d1_ha)
        h1_color = _last_color(h1_ha)
        m5_color = _last_color(m5_ha)
        m5_prev_color = _prev_color(m5_ha)

        # ----------------------------------------------------------------
        # 4) CIERRE: si la vela M5 cambió de color -> cerrar posición
        # ----------------------------------------------------------------
        if m5_color != m5_prev_color:
            # Hay cambio de color; cerramos cualquier posición abierta de este símbolo
            if await has_open_position(symbol):
                print(
                    f"[M5-HA][{symbol}] Cambio de color M5 "
                    f"{m5_prev_color} → {m5_color}. Cerrando posición abierta."
                )
                await self._close_all_positions_for_symbol(symbol)
                # Después de cerrar, no abrimos en el mismo tick;
                # esperamos a la próxima vela M5
                return

        # ----------------------------------------------------------------
        # 5) APERTURA: si no hay posición abierta, buscamos alineación D1/H1/M5
        # ----------------------------------------------------------------
        if await has_open_position(symbol):
            # Ya hay posición; no abrimos nada
            return

        # Todos verdes → BUY
        if d1_color == "green" and h1_color == "green" and m5_color == "green":
            print(
                f"[M5-HA][{symbol}] D1/H1/M5 VERDES. "
                f"Abriendo BUY volumen={self.volume}."
            )
            await open_market_order(symbol, "buy", self.volume)
            return

        # Todos rojos → SELL
        if d1_color == "red" and h1_color == "red" and m5_color == "red":
            print(
                f"[M5-HA][{symbol}] D1/H1/M5 ROJOS. "
                f"Abriendo SELL volumen={self.volume}."
            )
            await open_market_order(symbol, "sell", self.volume)
            return

    # -------------------------------------------------------------------
    # Helpers de gestión de posiciones
    # -------------------------------------------------------------------

    async def _close_all_positions_for_symbol(self, symbol: str) -> None:
        """
        Cierra TODAS las posiciones abiertas de ese símbolo (independientemente del lado).
        """
        positions = await get_open_positions()
        symbol_u = symbol.upper()

        for p in positions:
            pos_symbol = str(p.get("symbol") or "").upper()
            if pos_symbol != symbol_u:
                continue

            position_id = p.get("position_id")
            if position_id is None:
                continue

            try:
                print(
                    f"[M5-HA][{symbol_u}] Cerrando posición {position_id} "
                    f"(volume={p.get('volume')})"
                )
                await close_position(position_id)
            except Exception as exc:
                print(
                    f"❌ [M5-HA][{symbol_u}] Error al cerrar posición "
                    f"{position_id}: {exc!r}"
                )
