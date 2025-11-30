# app/api/routes_manual.py
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.trading.orders import open_risked_market_order, close_position_market

router = APIRouter(prefix="/manual", tags=["manual-trading"])

Side = Literal["long", "short"]


class ManualOpenRequest(BaseModel):
    instrument: str = Field(..., example="GBPUSD")
    side: Side = Field(..., example="long")
    # Lo dejamos por compatibilidad, pero por ahora NO se usa:
    entry_price: float | None = Field(
        None,
        description="Ignorado: la orden se abre siempre al precio actual del broker.",
    )


class ManualOpenResponse(BaseModel):
    status: str
    instrument: str
    side: Side
    entry_price: float
    volume: float
    stop_loss: float
    position_id: int
    raw: dict


@router.post("/open", response_model=ManualOpenResponse)
async def manual_open_position(payload: ManualOpenRequest):
    """
    Abre una operación a mercado con tamaño calculado por riesgo.

    Diferencias vs proyecto OANDA:
    - No usamos oanda_client ni entry_price manual.
    - El precio de entrada lo toma internamente open_risked_market_order()
      usando broker.get_current_price().
    - El candado "ya hay operación abierta" se maneja dentro de open_risked_market_order
      si el broker implementa has_open_position().
    """
    try:
        order = await open_risked_market_order(
            instrument=payload.instrument,
            side=payload.side,
            comment="Manual open",
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Funcionalidad no implementada aún en el broker: {e!r}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al abrir operación: {e!r}",
        )

    # Puede ser None si el candado bloqueó la operación
    if order is None:
        raise HTTPException(
            status_code=400,
            detail=f"Ya existe una posición abierta en {payload.instrument} para ese lado (según broker).",
        )

    return ManualOpenResponse(
        status="ok",
        instrument=payload.instrument,
        side=payload.side,
        entry_price=order["entry_price"],
        volume=order["volume"],
        stop_loss=order["stop_loss"],
        position_id=order["position_id"],
        raw=order["raw"],
    )


# ---------- CIERRE MANUAL ----------

class ManualCloseRequest(BaseModel):
    position_id: int = Field(..., example=123456789)


class ManualCloseResponse(BaseModel):
    status: str
    position_id: int
    response: dict


@router.post("/close", response_model=ManualCloseResponse)
async def manual_close_position(payload: ManualCloseRequest):
    """
    Cierra una posición a mercado por position_id.

    En el proyecto viejo cerrábamos por instrumento + side + units usando OANDA.
    En el nuevo stack genérico, close_position_market trabaja por ID de posición,
    que es mucho más estándar (cTrader, etc.).
    """
    try:
        resp = await close_position_market(position_id=payload.position_id)
    except NotImplementedError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Funcionalidad no implementada aún en el broker: {e!r}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al cerrar operación: {e!r}",
        )

    return ManualCloseResponse(
        status="closed",
        position_id=payload.position_id,
        response=resp,
    )
