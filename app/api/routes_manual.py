# app/api/routes_manual.py
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.broker import get_broker
from app.broker.base import Side  # "buy" | "sell"

router = APIRouter(prefix="/manual", tags=["manual"])


class ManualOpenOrderRequest(BaseModel):
    """
    Payload para abrir una orden manual.

    symbol: símbolo de cTrader, ej: "EURUSD", "GBPUSD"
    side:   "buy" o "sell"
    volume: volumen en unidades (int de Open API)
    """
    symbol: str = Field(..., description="Símbolo, ej: EURUSD")
    side: Side = Field(..., description="buy | sell")
    volume: float = Field(..., gt=0, description="Volumen en unidades (ej: 100000)")

class ManualCloseOrderRequest(BaseModel):
    position_id: int
    volume: Optional[float] = Field(
        None,
        description="Volumen a cerrar. Si es null, se cierra el 100%."
    )

@router.post("/open")
async def manual_open_order(body: ManualOpenOrderRequest):
    """
    Abre una orden de mercado manual usando el broker (cTrader / OpenAPI).

    Ejemplo de body JSON:

    {
      "symbol": "EURUSD",
      "side": "buy",
      "volume": 100000
    }
    """
    broker = get_broker()

    try:
        result = await broker.open_market_order(
            symbol=body.symbol,
            side=body.side,
            volume=body.volume,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al abrir operación manual: {e!r}",
        )

    return {
        "status": "ok",
        "symbol": body.symbol,
        "side": body.side,
        "volume": body.volume,
        "broker_result": result,
    }



@router.post("/close")
async def manual_close_order(body: ManualCloseOrderRequest):
    broker = get_broker()

    try:
        result = await broker.close_position(
            position_id=body.position_id,
            volume=body.volume,  # 👈 AHORA SÍ PASAMOS EL VOLUMEN (o None)
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al cerrar posición: {e!r}",
        )

    return {
        "status": "ok",
        "position_id": body.position_id,
        "requested_volume": body.volume,
        "broker_result": result,
    }
