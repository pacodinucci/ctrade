# app/trading/risk.py
from __future__ import annotations

from app.broker.base import ExecutionBroker

RISK_PER_TRADE = 0.02      # 2% por trade
STOP_POINTS = 200          # distancia del stop en "puntos" de precio


def get_point_value(instrument: str) -> float:
    """
    Devuelve el tamaño de 1 punto en PRECIO para el instrumento.

    Definimos "punto" como el mínimo tick del precio:
    - Pares con JPY (ej: GBP_JPY) → 3 decimales → 1 punto = 0.001
    - Resto (EUR_USD, GBP_USD, etc.) → 5 decimales → 1 punto = 0.00001
    """
    if instrument.endswith("JPY"):
        return 0.001
    return 0.00001


async def get_account_balance(broker: ExecutionBroker) -> float:
    """
    Envuelve broker.get_account_balance()
    """
    return await broker.get_account_balance()


async def calc_volume_for_risk(
    broker: ExecutionBroker,
    instrument: str,
    entry_price: float,
    stop_points: int = STOP_POINTS,
    risk_fraction: float = RISK_PER_TRADE,
) -> float:
    """
    Calcula el VOLUME que vamos a enviar al broker para arriesgar 'risk_fraction'
    del balance con un stop de 'stop_points' puntos de precio.

    Asumimos:
    - Cuenta en USD.
    - Para USD_XXX (ej: USD_JPY) el movimiento del stop está en XXX → lo pasamos a USD
      dividiendo por el entry_price.
    - Para XXX_USD (ej: EUR_USD, GBP_USD) el movimiento del stop ya está en USD.

    El 'volume' resultante es el que se pasará DIRECTAMENTE a CTraderBroker.open_market_order.
    """
    balance = await get_account_balance(broker)
    risk_amount = balance * risk_fraction

    point_size = get_point_value(instrument)         # tamaño de 1 punto en precio
    price_move_at_stop = point_size * stop_points    # movimiento del precio del par

    if price_move_at_stop <= 0:
        raise ValueError("price_move_at_stop <= 0, revisá STOP_POINTS")

    # Igual que antes:
    # - USD_JPY (USD_XXX) → convertir a USD
    # - EUR_USD / GBP_USD (XXX_USD) → ya está en USD
    if instrument.startswith("USD_"):
        risk_per_unit = price_move_at_stop / entry_price
    else:
        risk_per_unit = price_move_at_stop

    volume = risk_amount / risk_per_unit
    return float(volume)
