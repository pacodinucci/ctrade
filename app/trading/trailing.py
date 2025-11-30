# app/trading/trailing.py
from __future__ import annotations

from typing import Literal, Callable, Optional, Awaitable, Dict, Any
import asyncio
from datetime import datetime, timezone

from app.trading.orders import close_position_market
from app.broker import get_broker
from app.broker.base import ExecutionBroker

# En el bot original:
# Side = Literal["long", "short"]
Side = Literal["long", "short"]

CLOSE_DIST_POINTS = 200
TRAIL_STEP_POINTS = 1


def _get_point_size(instrument: str) -> float:
    """
    Misma lógica que en el bot anterior:
    - Pares con JPY → 0.001
    - Resto → 0.00001
    """
    if instrument.endswith("JPY"):
        return 0.001
    return 0.00001


# Tipos de callbacks (como en el código viejo)
OnUpdate = Callable[[dict], None]
# Ahora lo hacemos async para poder cerrar la posición dentro del callback
OnCloseSignal = Callable[[dict], Awaitable[None]]


# Tipo para obtener precios (abstrae OANDA / cTrader / lo que sea)
GetPrice = Callable[[], Awaitable[Dict[str, Any]]]
# Debe devolver algo como: {"price": float, "time": str}


async def stream_trailing_logic(
    instrument: str,
    side: Side,
    entry_price: float,
    units: float,
    get_price: GetPrice,
    on_update: Optional[OnUpdate] = None,
    on_close_signal: Optional[OnCloseSignal] = None,
    poll_seconds: float = 1.0,
):
    """
    Motor de trailing *puro* (sin tocar directamente ningún broker):

    - Calcula close_level inicial a CLOSE_DIST_POINTS puntos en contra.
    - Cada TRAIL_STEP_POINTS puntos a favor mueve close_level.
    - Cuando el precio toca close_level, emite un "close_signal".

    Diferencias con el código original:
    - En lugar de usar OANDA PricingStream, usamos un callback `get_price`
      que devuelve {"price": float, "time": str}.
    - El loop es async y espera `poll_seconds` entre lecturas de precio.

    La lógica de niveles (close_level, best_price, puntos, pnl) es la misma.
    """
    point_size = _get_point_size(instrument)
    close_dist = CLOSE_DIST_POINTS * point_size
    trail_step = TRAIL_STEP_POINTS * point_size

    if side == "long":
        close_level = entry_price - close_dist
        best_price = entry_price
    else:
        close_level = entry_price + close_dist
        best_price = entry_price

    # Evento inicial
    if on_update:
        on_update(
            {
                "event": "init",
                "instrument": instrument,
                "side": side,
                "entry_price": entry_price,
                "best_price": best_price,
                "close_level": close_level,
                "units": units,
                "price": entry_price,
                "time": datetime.now(timezone.utc).isoformat(),
            }
        )

    while True:
        tick = await get_price()
        price = float(tick["price"])
        time_ = tick.get("time") or datetime.now(timezone.utc).isoformat()

        moved = False

        if side == "long":
            # trailing a favor (subimos close_level)
            if price > best_price:
                move = price - best_price
                steps = int(move // trail_step)
                if steps > 0:
                    best_price += steps * trail_step
                    close_level += steps * trail_step
                    moved = True

            # ¿tocó el nivel de cierre lógico?
            if price <= close_level:
                if on_close_signal:
                    pnl = price - entry_price
                    puntos = pnl / point_size
                    await on_close_signal(
                        {
                            "event": "close_signal",
                            "instrument": instrument,
                            "side": side,
                            "price": price,
                            "time": time_,
                            "entry_price": entry_price,
                            "best_price": best_price,
                            "close_level": close_level,
                            "pnl": pnl,
                            "puntos": puntos,
                            "units": units,
                        }
                    )
                break

        else:  # short
            if price < best_price:
                move = best_price - price
                steps = int(move // trail_step)
                if steps > 0:
                    best_price -= steps * trail_step
                    close_level -= steps * trail_step
                    moved = True

            if price >= close_level:
                if on_close_signal:
                    pnl = entry_price - price
                    puntos = pnl / point_size
                    await on_close_signal(
                        {
                            "event": "close_signal",
                            "instrument": instrument,
                            "side": side,
                            "price": price,
                            "time": time_,
                            "entry_price": entry_price,
                            "best_price": best_price,
                            "close_level": close_level,
                            "pnl": pnl,
                            "puntos": puntos,
                            "units": units,
                        }
                    )
                break

        if moved and on_update:
            on_update(
                {
                    "event": "trail_update",
                    "instrument": instrument,
                    "side": side,
                    "price": price,
                    "time": time_,
                    "best_price": best_price,
                    "close_level": close_level,
                    "units": units,
                }
            )

        await asyncio.sleep(poll_seconds)

async def stream_and_trail_position(
    instrument: str,
    side: Side,              # "buy" | "sell"
    position_id: int,
    entry_price: float,
    units: float,
    bot_id: Optional[str] = None,
    broker: Optional[ExecutionBroker] = None,
    poll_seconds: float = 1.0,
):
    """
    Wrapper específico para nuestro bot:

    - Usa el broker (cTrader) para obtener precios con get_current_price().
    - Ejecuta la lógica de trailing puro (stream_trailing_logic).
    - Cuando hay señal de cierre, llama a close_position_market(position_id).
    """
    broker = broker or get_broker()

    async def _get_price() -> Dict[str, Any]:
        price = await broker.get_current_price(instrument)
        return {
            "price": price,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    def _on_update(event: dict) -> None:
        tag = bot_id or f"pos-{position_id}"
        print(
            f"[{tag}] TRAIL_UPDATE → "
            f"price={event['price']:.5f}, close_level={event['close_level']:.5f}"
        )

    async def _on_close_signal(event: dict) -> None:
        tag = bot_id or f"pos-{position_id}"
        print(
            f"[{tag}] CLOSE_SIGNAL → price={event['price']:.5f}, "
            f"pnl={event['pnl']:.5f} ({event['puntos']:.1f} puntos)"
        )
        await close_position_market(position_id=position_id, broker=broker)

    await stream_trailing_logic(
        instrument=instrument,
        side=side,
        entry_price=entry_price,
        units=units,
        get_price=_get_price,
        on_update=_on_update,
        on_close_signal=_on_close_signal,
        poll_seconds=poll_seconds,
    )