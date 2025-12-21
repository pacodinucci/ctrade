# app/api/routes_manual.py
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.broker import get_broker
from app.broker.base import Side  # "buy" | "sell"

# 👇 usamos directamente los helpers del servicio cTrader para esta prueba
from app.broker.ctrader_market_data import (
    open_market_order as md_open_market_order,
    set_position_sl_tp as md_set_position_sl_tp,
)

router = APIRouter(prefix="/manual", tags=["manual"])


class ManualOpenOrderRequest(BaseModel):
    """
    Payload para abrir una orden manual.

    symbol: símbolo de cTrader, ej: "EURUSD", "GBPUSD"
    side:   "buy" o "sell"
    volume: volumen en unidades (int de Open API)
    stop_loss / take_profit: precios absolutos (ej: 4257.93) OPCIONALES.
    """
    symbol: str = Field(..., description="Símbolo, ej: EURUSD")
    side: Side = Field(..., description="buy | sell")
    volume: float = Field(..., gt=0, description="Volumen en unidades (ej: 100000)")

    # Opcionales: si no los mandás, calculamos un SL/TP de prueba desde entry_price
    stop_loss: Optional[float] = Field(
        None,
        description="Precio absoluto de Stop Loss (ej: 4255.50). Si es null, se calculará por defecto.",
    )
    take_profit: Optional[float] = Field(
        None,
        description="Precio absoluto de Take Profit (ej: 4259.50). Si es null, se calculará por defecto.",
    )


class ManualCloseOrderRequest(BaseModel):
    position_id: int
    volume: Optional[float] = Field(
        None,
        description="Volumen a cerrar. Si es null, se cierra el 100%.",
    )


@router.post("/open")
async def manual_open_order(body: ManualOpenOrderRequest):
    """
    Abre una orden de mercado manual y, si es posible, setea SL/TP inmediatamente
    usando la API de cTrader (ProtoOAAmendPositionSLTPReq).

    Ejemplo de body JSON mínimo:

    {
      "symbol": "XAUUSD",
      "side": "buy",
      "volume": 100
    }

    Ejemplo con SL/TP explícitos:

    {
      "symbol": "XAUUSD",
      "side": "buy",
      "volume": 100,
      "stop_loss": 4270.0,
      "take_profit": 4280.0
    }
    """
    # 1) Abrimos la posición directamente contra el servicio cTrader
    try:
        opened = await md_open_market_order(
            symbol=body.symbol,
            side=body.side,
            volume=body.volume,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al abrir operación manual: {e!r}",
        )

    position_id = opened.get("position_id")
    entry_price = opened.get("entry_price")

    # Si no tenemos position_id o entry_price, devolvemos igual el resultado pero sin SL/TP
    if position_id is None or entry_price is None:
        return {
            "status": "partial",
            "detail": "Orden enviada, pero no se pudo confirmar position_id/entry_price. No se aplicó SL/TP.",
            "symbol": body.symbol,
            "side": body.side,
            "volume": body.volume,
            "opened": opened,
        }

    # 2) Determinamos SL/TP a usar
    sl = body.stop_loss
    tp = body.take_profit

    # Si no vinieron en el payload, calculamos un SL/TP de prueba sencillo
    if sl is None and tp is None:
        # Regla de prueba:
        #  - buy: SL = entry - 2.0 | TP = entry + 1.0
        #  - sell: SL = entry + 2.0 | TP = entry - 1.0
        if body.side == "buy":
            sl = entry_price - 2.0
            tp = entry_price + 1.0
        else:
            sl = entry_price + 2.0
            tp = entry_price - 1.0

    # 3) Aplicamos SL/TP en cTrader
    try:
        sl_tp_result = await md_set_position_sl_tp(
            symbol=body.symbol,
            position_id=position_id,
            stop_loss=sl,
            take_profit=tp,
        )
    except Exception as e:
        # La posición quedó abierta, pero falló el SL/TP
        raise HTTPException(
            status_code=500,
            detail=(
                f"Operación abierta (position_id={position_id}), "
                f"pero error al setear SL/TP: {e!r}"
            ),
        )

    return {
        "status": "ok",
        "symbol": body.symbol,
        "side": body.side,
        "volume": body.volume,
        "opened": opened,
        "sl_tp": sl_tp_result,
    }


@router.post("/close")
async def manual_close_order(body: ManualCloseOrderRequest):
    """
    Cierra una posición manualmente usando el broker (sigue usando la abstracción genérica).
    """
    broker = get_broker()

    try:
        result = await broker.close_position(
            position_id=body.position_id,
            volume=body.volume,  # puede ser None → cerrar 100%
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
