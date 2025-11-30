# app/api/routes_trades.py
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, HTTPException

from app.broker import get_broker

router = APIRouter(prefix="/trades", tags=["trades"])


def _map_position(p: Dict) -> Dict:
    """
    Normaliza una posición del broker a un formato similar al de OANDA.

    Esperamos que cada posición venga con algo como:
      - id / position_id
      - symbol / instrument
      - side: "buy" | "sell"
      - volume
      - price / entry_price
      - state (opcional)
      - stop_loss (opcional)
      - take_profit (opcional)
    """
    # id genérico
    pid = (
        p.get("id")
        or p.get("position_id")
        or p.get("positionId")
    )

    symbol = p.get("instrument") or p.get("symbol")
    side_raw = p.get("side")

    # normalizamos a "long"/"short"/"flat"
    if side_raw in ("buy", "long"):
        side = "long"
    elif side_raw in ("sell", "short"):
        side = "short"
    else:
        side = "flat"

    volume = float(p.get("volume", 0))
    price = float(p.get("price") or p.get("entry_price") or 0.0)

    return {
        "id": pid,
        "instrument": symbol,
        "side": side,
        "volume": volume,
        "price": price,
        "state": p.get("state", "open"),
        "stopLoss": p.get("stop_loss") or p.get("stopLossOrder"),
        "takeProfit": p.get("take_profit") or p.get("takeProfitOrder"),
    }


@router.get("/", summary="Listar todas las operaciones abiertas")
async def list_open_trades():
    """
    Devuelve todas las operaciones (trades/positions) abiertas en la cuenta,
    sin importar si las abrió el bot o manualmente.

    En el proyecto nuevo esto llama a broker.list_open_positions().
    """
    broker = get_broker()

    lister = getattr(broker, "list_open_positions", None)
    if lister is None:
        raise HTTPException(
            status_code=503,
            detail="El broker no implementa list_open_positions() todavía.",
        )

    try:
        positions = await lister()  # type: ignore[func-returns-value]
    except NotImplementedError:
        raise HTTPException(
            status_code=503,
            detail="list_open_positions() no está implementado todavía en el broker.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al obtener posiciones abiertas: {e!r}",
        )

    mapped = [_map_position(p) for p in positions]

    return {
        "count": len(mapped),
        "trades": mapped,
    }


@router.get("/{instrument}", summary="Listar operaciones abiertas por instrumento")
async def list_open_trades_by_instrument(instrument: str):
    """
    Devuelve las operaciones abiertas SOLO para un instrumento dado
    (ej: GBPUSD, EURUSD).
    """
    broker = get_broker()

    lister = getattr(broker, "list_open_positions", None)
    if lister is None:
        raise HTTPException(
            status_code=503,
            detail="El broker no implementa list_open_positions() todavía.",
        )

    try:
        positions = await lister()  # type: ignore[func-returns-value]
    except NotImplementedError:
        raise HTTPException(
            status_code=503,
            detail="list_open_positions() no está implementado todavía en el broker.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al obtener posiciones abiertas: {e!r}",
        )

    instrument_trades = [
        _map_position(p)
        for p in positions
        if (p.get("instrument") or p.get("symbol")) == instrument
    ]

    if not instrument_trades:
        raise HTTPException(
            status_code=404,
            detail=f"No hay operaciones abiertas en {instrument}",
        )

    return {
        "instrument": instrument,
        "count": len(instrument_trades),
        "trades": instrument_trades,
    }
