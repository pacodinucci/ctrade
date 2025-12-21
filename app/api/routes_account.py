# app/api/routes_account.py
from fastapi import APIRouter, HTTPException

from app.broker import get_broker
from app.broker.ctrader import CTraderBroker
from app.config import settings

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/summary")
async def account_summary():
    """
    Devuelve el resumen básico de la cuenta (balance).
    Útil para comprobar que la API y las credenciales funcionan.
    """
    broker = get_broker()

    try:
        balance = await broker.get_account_balance()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al obtener el balance: {e!r}",
        )

    return {
        "account_id": settings.CTRADER_ACCOUNT_ID,
        "broker": "ctrader",
        "balance": balance,
    }

@router.get("/info")
async def account_info():
    """
    Devuelve la información total de la cuenta (objeto JSON completo),
    filtrada por CTRADER_TRADER_ACCOUNT_ID.
    """
    broker = get_broker()

    if not isinstance(broker, CTraderBroker):
        raise HTTPException(
            status_code=500,
            detail="El broker actual no es CTraderBroker",
        )

    try:
        info = await broker.get_account_info()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener la informació de cuenta: {e!r}")
    
    return info

@router.get("/positions")
async def account_open_positions():
    """
    Devuelve las posiciones abiertas de la cuenta usando Open API
    (ProtoOAReconcileReq → ProtoOAReconcileRes.position).
    """
    broker = get_broker()

    if not isinstance(broker, CTraderBroker):
        raise HTTPException(
            status_code=500,
            detail="El broker actual no es CTraderBroker",
        )

    try:
        positions = await broker.list_open_positions()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al obtener las posiciones abiertas: {e!r}",
        )

    return {
        "account_id": settings.CTRADER_TRADER_ACCOUNT_ID,
        "positions": positions,
    }
    
