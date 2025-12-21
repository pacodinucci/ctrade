from __future__ import annotations

from typing import Literal, Optional, Dict, Any

from app.broker import get_broker
from app.broker.base import ExecutionBroker, Side
from app.trading.risk import (
    RISK_PER_TRADE,
    STOP_POINTS,
    get_point_value,
    calc_volume_for_risk,
)

SideBot = Literal["long", "short"]  # lo que usa el bot


def _map_side_bot_to_broker(side: SideBot) -> Side:
    """long/short (bot) → buy/sell (broker)."""
    return "buy" if side == "long" else "sell"


async def open_risked_market_order(
    instrument: str,
    side: SideBot,
    *,
    stop_points: int = STOP_POINTS,
    risk_per_trade: float = RISK_PER_TRADE,
    explicit_stop_loss: Optional[float] = None,
    explicit_take_profit: Optional[float] = None,
    comment: Optional[str] = None,
    broker: Optional[ExecutionBroker] = None,
) -> Optional[Dict[str, Any]]:
    """
    Abre una orden de mercado:

      - Tamaño (volume) calculado para arriesgar `risk_per_trade` del balance
        con un stop de `stop_points` puntos (si no se pasa stop explícito).
      - StopLoss en precio a esa distancia.
      - Sin TP salvo que se pase explícito.

    Devuelve:
      {
        "position_id": ...,
        "entry_price": ...,
        "volume": ...,
        "stop_loss": ...,
        "raw": {...}
      }
    """
    broker = broker or get_broker()

    # 🔹 1) Precio actual
    current_price = await broker.get_current_price(instrument)

    # 🔹 2) Determinar stop en precio
    point_size = get_point_value(instrument)

    if explicit_stop_loss is not None:
        stop_loss_price = explicit_stop_loss
        stop_dist_price = abs(current_price - stop_loss_price)
        stop_points_eff = int(stop_dist_price / point_size)
    else:
        stop_dist_price = stop_points * point_size
        stop_points_eff = stop_points

        if side == "long":
            stop_loss_price = current_price - stop_dist_price
        else:
            stop_loss_price = current_price + stop_dist_price

    # 🔹 3) Calcular VOLUME según riesgo (el que vamos a pasar DIRECTO al broker)
    volume = await calc_volume_for_risk(
        broker=broker,
        instrument=instrument,
        entry_price=current_price,
        stop_points=stop_points,
        risk_fraction=risk_per_trade,
    )

    # Logs estilo bot viejo
    print(f"[ORDERS] Volume calculado (riesgo): {volume:.2f}")
    print(
        f"[ORDERS] entry={current_price:.5f}, "
        f"stop_dist={stop_dist_price:.5f}, "
        f"stop_price={stop_loss_price:.5f} "
        f"({stop_points_eff} puntos)"
    )

    broker_side: Side = _map_side_bot_to_broker(side)

    # 🔹 4) Enviar orden al broker → volume se pasa TAL CUAL
    raw = await broker.open_market_order(
        symbol=instrument,
        side=broker_side,
        volume=volume,
        stop_loss=stop_loss_price,
        take_profit=explicit_take_profit,
        comment=comment or "Bot order (risk-based)",
    )

    position_id = (
        raw.get("position_id")
        or raw.get("id")
        or raw.get("positionId")
    )

    entry_price = raw.get("entry_price") or raw.get("price") or current_price

    # 👇 en lugar de levantar error, usamos -1 si no viene
    if position_id is None:
        position_id = -1

    return {
        "position_id": int(position_id),
        "entry_price": float(entry_price),
        "volume": float(volume),
        "stop_loss": float(stop_loss_price),
        "raw": raw,
    }



async def close_position_market(
    position_id: int,
    broker: Optional[ExecutionBroker] = None,
) -> Dict[str, Any]:
    broker = broker or get_broker()
    return await broker.close_position(position_id)

async def _has_open_trade_for_instrument(
    broker: ExecutionBroker,
    instrument: str,
    side: SideBot,
) -> bool:
    # 🔒 Por ahora: candado desactivado mientras probamos cTrader
    return False
