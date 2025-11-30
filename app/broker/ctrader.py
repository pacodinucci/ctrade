# app/broker/ctrader.py
from __future__ import annotations

from typing import Dict, Any, Optional, List

import httpx

from app.config import get_settings
from app.broker.base import ExecutionBroker, Side


class CTraderBroker(ExecutionBroker):
    """
    Implementación de ExecutionBroker para cTrader / Spotware.
    Maneja:
      - Tokens OAuth2 (por ahora solo los lee de settings)
      - Llamados a la API para precios, órdenes y posiciones (por implementar).
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=self.settings.CTRADER_API_BASE_URL.rstrip("/"),
            timeout=15.0,
        )

        # Tokens iniciales tomados de config (luego ideal: DB / refresh flow)
        self._access_token: Optional[str] = self.settings.CTRADER_ACCESS_TOKEN
        self._refresh_token: Optional[str] = self.settings.CTRADER_REFRESH_TOKEN

    # ---------- Gestión de tokens ----------

    async def _ensure_access_token(self) -> str:
        """
        Garantiza que tengamos un access_token válido.
        Por ahora asumimos que el token configurado en .env es válido.
        Más adelante: implementar refresh con el refresh_token.
        """
        if not self._access_token:
            raise RuntimeError("No access token configured for cTrader")

        return self._access_token

    async def _authorized_headers(self) -> Dict[str, str]:
        token = await self._ensure_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ---------- Métodos de la interfaz ExecutionBroker ----------

    async def get_account_balance(self) -> float:
        """
        Devuelve el balance de la cuenta self.settings.CTRADER_ACCOUNT_ID.

        TODO: reemplazar por el endpoint real de cTrader para account summary.
        """
        headers = await self._authorized_headers()

        # Ejemplo-esqueleto (POR AHORA COMENTADO):
        # resp = await self._client.get(
        #     f"/trading/accounts/{self.settings.CTRADER_ACCOUNT_ID}/summary",
        #     headers=headers,
        # )
        # resp.raise_for_status()
        # data = resp.json()
        # return float(data["balance"])

        raise NotImplementedError("get_account_balance() not implemented for cTrader yet")

    async def get_current_price(self, symbol: str) -> float:
        """
        Devuelve el precio actual (mid/last) del símbolo.

        TODO: implementar llamada real a la API de precios de cTrader.
        """
        headers = await self._authorized_headers()

        # Ejemplo-esqueleto:
        # resp = await self._client.get(
        #     "/trading/prices",
        #     params={
        #         "symbol": symbol,
        #         "accountId": self.settings.CTRADER_ACCOUNT_ID,
        #     },
        #     headers=headers,
        # )
        # resp.raise_for_status()
        # data = resp.json()
        # price = float(data["price"])
        # return price

        raise NotImplementedError("get_current_price() not implemented for cTrader yet")

    async def open_market_order(
        self,
        symbol: str,
        side: Side,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Abre una orden de mercado en cTrader.

        IMPORTANTE:
        - `volume` es el valor que viene de nuestra lógica de riesgo (calc_volume_for_risk).
        - Aquí asumimos que ese número se puede enviar DIRECTAMENTE como volume
          al API de cTrader.
        - Si en la práctica cTrader requiere otra escala (ej.: * 100, * 100000),
          la conversión se hará **SOLO aquí**, sin tocar risk.py ni orders.py.
        """
        headers = await self._authorized_headers()

        # Ejemplo-esqueleto de payload (ajustar al formato real de cTrader):
        # payload = {
        #     "accountId": self.settings.CTRADER_ACCOUNT_ID,
        #     "symbol": symbol,
        #     "side": "BUY" if side == "buy" else "SELL",
        #     "volume": volume,
        #     "type": "MARKET",
        #     "stopLoss": stop_loss,
        #     "takeProfit": take_profit,
        #     "comment": comment,
        # }
        # resp = await self._client.post("/trading/orders", json=payload, headers=headers)
        # resp.raise_for_status()
        # data = resp.json()
        # return data

        raise NotImplementedError("open_market_order() not implemented for cTrader yet")

    async def close_position(self, position_id: int) -> Dict[str, Any]:
        """
        Cierra una posición abierta por ID en cTrader.
        """
        headers = await self._authorized_headers()

        # Ejemplo-esqueleto:
        # resp = await self._client.post(
        #     f"/trading/positions/{position_id}/close",
        #     json={"accountId": self.settings.CTRADER_ACCOUNT_ID},
        #     headers=headers,
        # )
        # resp.raise_for_status()
        # return resp.json()

        raise NotImplementedError("close_position() not implemented for cTrader yet")

    async def has_open_position(self, symbol: str, side: Side) -> bool:
        """
        Devuelve True si hay una posición abierta en 'symbol' y 'side'.

        Se usa para el candado de open_risked_market_order.
        Si todavía no tenés la lógica lista, podés devolver siempre False
        o lanzar NotImplementedError para que el candado quede inactivo.
        """
        # Implementación mínima por ahora: sin candado real.
        # return False

        raise NotImplementedError("has_open_position() not implemented for cTrader yet")

    async def list_open_positions(self) -> List[Dict[str, Any]]:
        """
        Devuelve la lista de posiciones abiertas.

        Se usa en routes_trades.py para listar todas las operaciones abiertas.
        """
        headers = await self._authorized_headers()

        # Ejemplo-esqueleto:
        # resp = await self._client.get(
        #     f"/trading/accounts/{self.settings.CTRADER_ACCOUNT_ID}/positions",
        #     headers=headers,
        # )
        # resp.raise_for_status()
        # data = resp.json()
        # return data["positions"]

        raise NotImplementedError("list_open_positions() not implemented for cTrader yet")

    # ---------- Limpieza ----------

    async def aclose(self) -> None:
        await self._client.aclose()
