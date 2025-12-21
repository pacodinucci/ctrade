# app/api/routes_test_watcher.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.broker.ctrader_market_data import open_market_order
from app.trading.position_watcher import (
    position_watcher_manager,
    PositionWatchConfig,
)

router = APIRouter(prefix="/test-watcher", tags=["test-watcher"])


class OpenAndWatchBody(BaseModel):
    symbol: str = Field(..., example="EURUSD")
    side: str = Field(..., pattern="^(buy|sell)$", example="buy")
    volume: float = Field(..., example=100000)
    max_move_price: float = Field(..., example=0.0001)


@router.post("/open-and-watch")
async def open_and_watch(body: OpenAndWatchBody):
    """
    Abre una posición de mercado y arranca un watcher que la cierra
    cuando el precio se mueve `max_move_price` (arriba o abajo).
    """
    try:
        broker_result = await open_market_order(
            body.symbol,
            body.side,
            body.volume,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al abrir la operación: {e!r}",
        )

    position_id = broker_result.get("position_id")
    entry_price = broker_result.get("entry_price")

    if position_id is None or entry_price is None:
        # Si esto pasa, algo está mal en open_market_order / reconcile
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo determinar position_id / entry_price: {broker_result!r}",
        )

    config = PositionWatchConfig(max_move_price=body.max_move_price)

    # arrancamos el watcher (no bloquea la ruta)
    await position_watcher_manager.start(
        position_id=position_id,
        symbol=body.symbol,
        side=body.side,
        entry_price=entry_price,
        config=config,
    )

    return {
        "status": "ok",
        "symbol": body.symbol,
        "side": body.side,
        "volume": body.volume,
        "position_id": position_id,
        "entry_price": entry_price,
        "max_move_price": body.max_move_price,
    }
