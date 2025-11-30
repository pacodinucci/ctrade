# app/api/routes_account.py
from fastapi import APIRouter, HTTPException

from app.broker import get_broker
from app.config import settings

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/summary")
async def account_summary():
    """
    Devuelve el resumen básico de la cuenta (balance).

    Equivalente a la ruta del proyecto OANDA, pero usando el broker genérico.
    Útil para comprobar que la API y las credenciales funcionan.
    """
    broker = get_broker()

    try:
        balance = await broker.get_account_balance()
    except NotImplementedError:
        # Por si todavía no implementamos este método en CTraderBroker
        raise HTTPException(
            status_code=503,
            detail="get_account_balance() no está implementado todavía en el broker.",
        )

    return {
        "account_id": settings.CTRADER_ACCOUNT_ID,
        "broker": "ctrader",
        "balance": balance,
    }
