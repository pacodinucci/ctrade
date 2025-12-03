# app/broker/ctrader_market_data.py
from __future__ import annotations

import asyncio
import threading
import time
from typing import Dict, Optional, Set, List, Any
from dataclasses import dataclass

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg
from twisted.internet import reactor  # type: ignore

from app.config import get_settings

settings = get_settings()

# CTRADER_TRADER_ACCOUNT_ID = ctidTraderAccountId (accountId real)
CTID_TRADER_ACCOUNT_ID = int(settings.CTRADER_TRADER_ACCOUNT_ID)

@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    mid: float
    timestamp: float

class CTraderMarketDataService:
    """
    Servicio de market data usando cTrader Open API (Spotware).
    Solo se encarga de:
      - conectar
      - autenticarse
      - cargar símbolos
      - suscribirse a spots
      - exponer get_price(symbol)
    """


    def __init__(self) -> None:
        self._client: Optional[Client] = None

        # symbolName -> symbolId
        self._symbol_ids: Dict[str, int] = {}
        # symbolId -> symbolName
        self._id_to_symbol: Dict[int, str] = {}

        # symbolName -> último quote
        self._latest_quotes: Dict[str, Quote] = {}

        # símbolos a los que ya nos suscribimos
        self._subscribed: Set[str] = set()

        # Evento para saber cuándo ya tenemos la lista de símbolos
        self._symbols_ready = asyncio.Event()

        # futures esperando ejecución por clientOrderId (para órdenes nuevas)
        self._pending_orders: Dict[str, asyncio.Future] = {}

        # Lanzamos el reactor de Twisted en un thread aparte
        self._start_reactor_thread()

    # ---------- Twisted / OpenAPI setup ----------

    def _start_reactor_thread(self) -> None:
        t = threading.Thread(target=self._run_reactor, daemon=True)
        t.start()

    def _run_reactor(self) -> None:
        client = Client(
            EndPoints.PROTOBUF_DEMO_HOST,
            EndPoints.PROTOBUF_PORT,
            TcpProtocol,
        )
        self._client = client

        client.setConnectedCallback(self._on_connected)
        client.setDisconnectedCallback(self._on_disconnected)
        client.setMessageReceivedCallback(self._on_message_received)

        client.startService()
        reactor.run(installSignalHandlers=False)  # type: ignore

    # ---------- Callbacks de conexión ----------

    def _on_connected(self, client: Client) -> None:
        print("🔌 [MD] Conectado al proxy DEMO (market data)")

        req = OAMsg.ProtoOAApplicationAuthReq()
        req.clientId = settings.CTRADER_CLIENT_ID
        req.clientSecret = settings.CTRADER_CLIENT_SECRET

        d = client.send(req)
        d.addErrback(self._on_error)

    def _on_disconnected(self, client: Client, reason) -> None:
        print("🔌 [MD] Desconectado:", reason)

    def _on_message_received(self, client: Client, message) -> None:
        decoded = Protobuf.extract(message)

        if isinstance(decoded, OAMsg.ProtoOAApplicationAuthRes):
            self._on_app_auth_res(client, decoded)

        elif isinstance(decoded, OAMsg.ProtoOAAccountAuthRes):
            self._on_account_auth_res(client, decoded)

        elif isinstance(decoded, OAMsg.ProtoOASymbolsListRes):
            self._on_symbols_list_res(client, decoded)

        elif isinstance(decoded, OAMsg.ProtoOASpotEvent):
            self._on_spot_event(decoded)

        elif isinstance(decoded, OAMsg.ProtoOAExecutionEvent):  
            self._on_execution_event(decoded)

        elif isinstance(decoded, OAMsg.ProtoOAErrorRes):
            print("❌ [MD] ProtoOAErrorRes recibido:")
            print(decoded)

    def _on_error(self, failure) -> None:
        print("❌ [MD] Error en mensaje (Deferred):", failure)

    # ---------- Flujos de auth ----------

    def _on_app_auth_res(
        self,
        client: Client,
        decoded: OAMsg.ProtoOAApplicationAuthRes,
    ) -> None:
        print("✅ [MD] Application AUTH OK")

        req = OAMsg.ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
        req.accessToken = settings.CTRADER_ACCESS_TOKEN

        d = client.send(req)
        d.addErrback(self._on_error)

    def _on_account_auth_res(
        self,
        client: Client,
        decoded: OAMsg.ProtoOAAccountAuthRes,
    ) -> None:
        print("✅ [MD] Account AUTH OK, pidiendo lista de símbolos...")

        req = OAMsg.ProtoOASymbolsListReq()
        req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID

        d = client.send(req)
        d.addErrback(self._on_error)

    # ---------- Símbolos y spots ----------

    def _on_symbols_list_res(
        self,
        client: Client,
        decoded: OAMsg.ProtoOASymbolsListRes,
    ) -> None:
        print("✅ [MD] Symbols list recibida")

        for s in decoded.symbol:
            name = getattr(s, "symbolName", None)
            sid = getattr(s, "symbolId", None)
            if name is None or sid is None:
                continue

            name_u = str(name).upper()
            self._symbol_ids[name_u] = int(sid)
            self._id_to_symbol[int(sid)] = name_u

        loop = self._get_asyncio_loop()
        if loop is not None:
            loop.call_soon_threadsafe(self._symbols_ready.set)

    # def _on_spot_event(self, decoded: OAMsg.ProtoOASpotEvent) -> None:
    #     symbol_id = decoded.symbolId
    #     bid = decoded.bid
    #     ask = decoded.ask
    #     mid = (bid + ask) / 2

    #     symbol_name = self._id_to_symbol.get(symbol_id)
    #     if symbol_name is None:
    #         return

    #     self._latest_mid[symbol_name] = mid

    def _on_spot_event(self, decoded: OAMsg.ProtoOASpotEvent) -> None:
        symbol_id = decoded.symbolId
        bid = decoded.bid
        ask = decoded.ask
        mid = (bid + ask) / 2.0
        ts = time.time()

        symbol_name = self._id_to_symbol.get(symbol_id)
        if symbol_name is None:
            return

        quote = Quote(
            symbol=symbol_name,
            bid=float(bid),
            ask=float(ask),
            mid=float(mid),
            timestamp=ts,
        )

        self._latest_quotes[symbol_name] = quote

    def _on_execution_event(self, decoded: OAMsg.ProtoOAExecutionEvent) -> None:
        """
        Maneja ProtoOAExecutionEvent para matchear con una orden enviada
        vía clientOrderId y completar el Future correspondiente.
        """
        client_order_id = getattr(decoded, "clientOrderId", None)
        if not client_order_id:
            return

        fut = self._pending_orders.pop(client_order_id, None)
        if fut is None:
            # No estábamos esperando nada para este clientOrderId
            print("[MD] ExecutionEvent sin future registrado:", client_order_id)
            return

        result = {
            "position_id": getattr(decoded, "positionId", None),
            "price": getattr(decoded, "executionPrice", None),
            "volume": getattr(decoded, "executedVolume", None),
            "side": getattr(decoded, "tradeSide", None),
            "timestamp": getattr(decoded, "executionTime", None),
        }

        loop = self._get_asyncio_loop()
        if loop is None:
            fut.set_result(result)
        else:
            loop.call_soon_threadsafe(fut.set_result, result)


    # ---------- Helpers async ----------

    def _get_asyncio_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    async def _ensure_symbols_loaded(self, timeout: float = 5.0) -> None:
        if self._symbol_ids:
            return

        try:
            await asyncio.wait_for(self._symbols_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                "Timeout esperando lista de símbolos desde cTrader Open API"
            )

    async def _subscribe_symbol_if_needed(self, symbol: str) -> None:
        symbol_u = symbol.upper()

        await self._ensure_symbols_loaded()

        if symbol_u not in self._symbol_ids:
            raise RuntimeError(f"Símbolo {symbol_u} no encontrado en lista de símbolos")

        if symbol_u in self._subscribed:
            return

        symbol_id = self._symbol_ids[symbol_u]

        if self._client is None:
            raise RuntimeError("Cliente OpenAPI aún no inicializado")

        req = OAMsg.ProtoOASubscribeSpotsReq()
        req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
        req.symbolId.append(symbol_id)
        req.subscribeToSpotTimestamp = False

        d = self._client.send(req)
        d.addErrback(self._on_error)

        self._subscribed.add(symbol_u)
        print(f"📡 [MD] Suscripto a spots de {symbol_u} (symbolId={symbol_id})")

    # async def get_price(self, symbol: str, timeout: float = 5.0) -> float:
    #     """
    #     Devuelve el último mid de mercado (bid+ask)/2 para el símbolo dado.
    #     """
    #     symbol_u = symbol.upper()

    #     await self._subscribe_symbol_if_needed(symbol_u)

    #     elapsed = 0.0
    #     interval = 0.05

    #     while elapsed < timeout:
    #         if symbol_u in self._latest_mid:
    #             return float(self._latest_mid[symbol_u])

    #         await asyncio.sleep(interval)
    #         elapsed += interval

    #     raise RuntimeError(
    #         f"Timeout esperando spot de {symbol_u} desde cTrader Open API"
    #     )

    async def get_price(self, symbol: str, timeout: float = 5.0) -> float:
        symbol_u = symbol.upper()

        await self._subscribe_symbol_if_needed(symbol_u)

        elapsed = 0.0
        interval = 0.05

        while elapsed < timeout:
            q = self.get_last_quote(symbol_u)
            if q:
                return q.mid

            await asyncio.sleep(interval)
            elapsed += interval

        raise RuntimeError(f"Timeout esperando spot de {symbol_u} desde cTrader Open API")

    
    async def get_open_positions(self, timeout: float = 5.0) -> List[Dict[str, Any]]:
        """
        Devuelve un snapshot de posiciones abiertas usando ProtoOAReconcileReq.
        """
        if self._client is None:
            raise RuntimeError("Cliente OpenAPI aún no inicializado")

        # Aseguramos que ya tenemos la cuenta autenticada y symbols list
        await self._ensure_symbols_loaded()

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        # Armamos el request de reconcile
        req = OAMsg.ProtoOAReconcileReq()
        req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID

        def _on_reconcile(message):
            try:
                decoded = Protobuf.extract(message)  # ProtoOAReconcileRes
                positions_list: List[Dict[str, Any]] = []

                for p in decoded.position:
                    # Campos directos de la posición
                    position_id = getattr(p, "positionId", None)
                    open_price = (
                        getattr(p, "openPrice", None)
                        or getattr(p, "price", None)
                    )

                    # Campos dentro de tradeData
                    trade_data = getattr(p, "tradeData", None)

                    symbol_id = None
                    volume = None
                    trade_side = None
                    open_timestamp = None

                    if trade_data is not None:
                        symbol_id = getattr(trade_data, "symbolId", None)
                        volume = getattr(trade_data, "volume", None)
                        trade_side = getattr(trade_data, "tradeSide", None)
                        open_timestamp = getattr(trade_data, "openTimestamp", None)

                    symbol_name = None
                    if symbol_id is not None:
                        symbol_name = self._id_to_symbol.get(int(symbol_id))

                    positions_list.append(
                        {
                            "position_id": int(position_id) if position_id is not None else None,
                            "symbol_id": int(symbol_id) if symbol_id is not None else None,
                            "symbol": symbol_name,
                            "volume": int(volume) if volume is not None else None,
                            "trade_side": int(trade_side) if trade_side is not None else None,
                            "open_price": float(open_price) if open_price is not None else None,
                            "open_timestamp": int(open_timestamp) if open_timestamp is not None else None,
                        }
                    )

                loop.call_soon_threadsafe(future.set_result, positions_list)
            except Exception as e:
                loop.call_soon_threadsafe(future.set_exception, e)

        def _on_error_deferred(failure):
            loop.call_soon_threadsafe(
                future.set_exception,
                RuntimeError(f"Error en ProtoOAReconcileReq: {failure!r}"),
            )

        d = self._client.send(req)
        d.addCallback(_on_reconcile)
        d.addErrback(_on_error_deferred)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result  # lista de diccionarios
        except asyncio.TimeoutError:
            raise RuntimeError("Timeout esperando ProtoOAReconcileRes con posiciones abiertas")
        
    async def has_open_position(
        self,
        symbol: str,
        side: Optional[str] = None,  # "buy" | "sell" | None
    ) -> bool:
        """
        Devuelve True si hay al menos una posición abierta para ese símbolo.
        Si se pasa `side`, además debe coincidir el lado:

        - side="buy"  -> trade_side == 1
        - side="sell" -> trade_side == 2
        """
        positions = await self.get_open_positions()

        symbol_u = symbol.upper()
        desired_side = side.lower() if side else None

        for pos in positions:
            pos_symbol = str(pos.get("symbol") or "").upper()
            if pos_symbol != symbol_u:
                continue

            # Si no filtramos por lado, con que haya una posición del símbolo alcanza
            if desired_side is None:
                return True

            trade_side = pos.get("trade_side")  # 1 = BUY, 2 = SELL
            if trade_side is None:
                continue

            if trade_side == 1 and desired_side == "buy":
                return True
            if trade_side == 2 and desired_side == "sell":
                return True

        return False
    

    async def open_market_order(
        self,
        symbol: str,
        side: str,          # "buy" | "sell"
        volume: float,
        timeout: float = 5.0,
    ) -> Dict[str, Any]:
        """
        Abre una orden de mercado REAL vía Open API.

        - Enviamos ProtoOANewOrderReq.
        - Registramos un Future por clientOrderId para capturar ProtoOAExecutionEvent.
        - Intentamos esperar hasta `timeout` segundos.
        - Si hay timeout, buscamos la posición recién abierta con ProtoOAReconcileReq.
        """
        if self._client is None:
            raise RuntimeError("Cliente OpenAPI aún no inicializado")

        symbol_u = symbol.upper()
        await self._ensure_symbols_loaded()

        if symbol_u not in self._symbol_ids:
            raise RuntimeError(f"Símbolo {symbol_u} no encontrado en lista de símbolos")

        symbol_id = self._symbol_ids[symbol_u]

        req = OAMsg.ProtoOANewOrderReq()
        req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
        req.symbolId = symbol_id
        req.volume = int(volume)   # Open API espera int
        req.orderType = 1          # 1 = MARKET

        # 1 = BUY, 2 = SELL
        if side.lower() == "buy":
            req.tradeSide = 1
            desired_trade_side = 1
        else:
            req.tradeSide = 2
            desired_trade_side = 2

        client_order_id = str(int(time.time() * 1000))
        req.clientOrderId = client_order_id

        print(
            f"[MD] Enviando MARKET order → symbol={symbol_u}, "
            f"side={side}, volume={volume}, symbolId={symbol_id}, clientOrderId={client_order_id}"
        )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_orders[client_order_id] = fut

        d = self._client.send(req)
        d.addErrback(self._on_error)

        try:
            # 🔹 Camino ideal: llega el ExecutionEvent
            result = await asyncio.wait_for(fut, timeout=timeout)

            position_id = result.get("position_id")
            price = result.get("price")
            executed_volume = result.get("volume")

            return {
                "position_id": position_id,
                "entry_price": price,
                "volume": executed_volume,
                "side": side,
                "raw": result,
            }

        except asyncio.TimeoutError:
            # 🔹 Plan B: no llegó el ExecutionEvent, intentamos identificar la posición
            print(
                "[MD] ⚠ Timeout esperando ExecutionEvent; intentando identificar posición vía Reconcile"
            )
            self._pending_orders.pop(client_order_id, None)

            try:
                positions = await self.get_open_positions()

                # Filtramos por símbolo y lado
                candidates = [
                    p
                    for p in positions
                    if str(p.get("symbol") or "").upper() == symbol_u
                    and p.get("trade_side") == desired_trade_side
                ]

                if candidates:
                    # Tomamos la más reciente por open_timestamp (si existe)
                    def key_pos(p: Dict[str, Any]):
                        ts = p.get("open_timestamp")
                        return ts if isinstance(ts, int) else 0

                    last_pos = max(candidates, key=key_pos)

                    position_id = last_pos.get("position_id")
                    price = last_pos.get("open_price")
                    executed_volume = last_pos.get("volume")

                    print(
                        f"[MD] Posición identificada por reconcile → "
                        f"position_id={position_id}, price={price}, volume={executed_volume}"
                    )

                    return {
                        "position_id": position_id,
                        "entry_price": price,
                        "volume": executed_volume,
                        "side": side,
                        "raw": {
                            "identified_via": "reconcile",
                            "position": last_pos,
                        },
                    }

                # Si no encontramos nada, volvemos al fallback "sent"
                print("[MD] ⚠ No se encontró posición coincidente en Reconcile")

            except Exception as e:
                print("[MD] ⚠ Error intentando identificar posición vía Reconcile:", repr(e))

            # Fallback final: sabemos que se envió, pero sin position_id
            return {
                "position_id": None,
                "entry_price": None,
                "volume": volume,
                "side": side,
                "raw": {
                    "sent": True,
                    "symbol": symbol_u,
                    "symbolId": symbol_id,
                    "volume": volume,
                    "side": side,
                    "clientOrderId": client_order_id,
                },
            }

        
    async def close_position(
        self,
        position_id: int,
        volume: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Cierra una posición vía ProtoOAClosePositionReq.

        - position_id: el positionId real de cTrader.
        - volume:
            * None → buscamos la posición y cerramos TODO el volumen.
            * valor numérico → cantidad a cerrar (int Open API).
        """
        if self._client is None:
            raise RuntimeError("Cliente OpenAPI aún no inicializado")

        # Nos aseguramos de que la sesión está fully inicializada
        await self._ensure_symbols_loaded()

        # Si no nos pasan volumen, buscamos la posición y usamos su volumen total
        if volume is None:
            positions = await self.get_open_positions()
            target = None
            for p in positions:
                if p.get("position_id") == position_id:
                    target = p
                    break

            if target is None:
                raise RuntimeError(
                    f"No se encontró la posición {position_id} entre las posiciones abiertas"
                )

            vol = target.get("volume")
            if vol is None:
                raise RuntimeError(
                    f"No se encontró 'volume' para la posición {position_id} en Reconcile"
                )

            volume = float(vol)

        req = OAMsg.ProtoOAClosePositionReq()
        req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
        req.positionId = int(position_id)
        req.volume = int(volume)  # ahora siempre > 0

        print(
            f"[MD] Enviando CLOSE position → positionId={position_id}, volume={req.volume}"
        )

        d = self._client.send(req)
        d.addErrback(self._on_error)

        # De momento no esperamos ExecutionEvent de cierre
        return {
            "sent": True,
            "positionId": position_id,
            "volume": volume,
        }





# Singleton global
_market_data_service = CTraderMarketDataService()


async def get_current_price(symbol: str) -> float:
    """
    Helper que usa el singleton de market data.
    """
    return await _market_data_service.get_price(symbol)

async def get_open_positions() -> List[Dict[str, Any]]:
    """
    Helper global para obtener posiciones abiertas usando el singleton.
    """
    return await _market_data_service.get_open_positions()

async def has_open_position(
    symbol: str,
    side: Optional[str] = None,
) -> bool:
    """
    Helper global para saber si hay una posición abierta para un símbolo (y lado opcional).
    """
    return await _market_data_service.has_open_position(symbol, side)

async def open_market_order(symbol: str, side: str, volume: float) -> Dict[str, Any]:
    """
    Helper global para abrir una orden de mercado usando el singleton.
    """
    return await _market_data_service.open_market_order(symbol, side, volume)

async def close_position(
    position_id: int,
    volume: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Helper global para cerrar una posición usando el singleton.
    """
    return await _market_data_service.close_position(position_id, volume)

def get_last_quote(self, symbol: str) -> Optional[Quote]:
    symbol_u = symbol.upper()
    return self._latest_quotes.get(symbol_u)

def get_last_bid(self, symbol: str) -> Optional[float]:
    q = self.get_last_quote(symbol)
    return q.bid if q else None

def get_last_ask(self, symbol: str) -> Optional[float]:
    q = self.get_last_quote(symbol)
    return q.ask if q else None

def get_last_mid(self, symbol: str) -> Optional[float]:
    q = self.get_last_quote(symbol)
    return q.mid if q else None
