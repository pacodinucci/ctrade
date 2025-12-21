# app/api/routes_ws_prices.py
from __future__ import annotations

import asyncio
from typing import Dict, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.broker.ctrader_market_data import (
    subscribe_quotes,
    unsubscribe_quotes,
    Quote,
)

router = APIRouter(prefix="/ws", tags=["ws"])


@router.websocket("/prices")
async def ws_prices(websocket: WebSocket):
    """
    WebSocket de precios en tiempo real.

    Protocolo básico:

    1) El cliente se conecta a ws://.../ws/prices
    2) Envía un mensaje inicial JSON:
       { "type": "subscribe", "symbols": ["EURUSD", "GBPUSD", "XAUUSD"] }

    3) El server se suscribe internamente y empieza a enviar mensajes:
       {
         "type": "quote",
         "symbol": "EURUSD",
         "bid": 1.23456,
         "ask": 1.23470,
         "mid": 1.23463,
         "timestamp": 1710000000.123
       }
    """
    await websocket.accept()

    queues_by_symbol: Dict[str, asyncio.Queue[Quote]] = {}
    sender_tasks: List[asyncio.Task] = []

    async def sender(symbol: str, queue: asyncio.Queue[Quote]):
        """Toma quotes de la queue y los manda al WebSocket."""
        try:
            while True:
                quote = await queue.get()

                payload = {
                    "type": "quote",
                    "symbol": quote.symbol,
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "mid": quote.mid,
                    "timestamp": quote.timestamp,
                }
                await websocket.send_json(payload)
        except asyncio.CancelledError:
            # normal cuando cerramos el WS
            pass

    try:
        # 1) Mensaje inicial del cliente con los símbolos
        initial = await websocket.receive_json()

        if not isinstance(initial, dict) or initial.get("type") != "subscribe":
            # protocolo inválido
            await websocket.close(code=1003)
            return

        symbols = initial.get("symbols") or []
        if not isinstance(symbols, list) or not symbols:
            await websocket.close(code=1003)
            return

        # 2) Suscribirnos internamente a cada símbolo
        for raw in symbols:
            if not isinstance(raw, str):
                continue

            symbol = raw.upper()
            if symbol in queues_by_symbol:
                continue  # evitar duplicados

            queue = await subscribe_quotes(symbol)
            queues_by_symbol[symbol] = queue

            task = asyncio.create_task(
                sender(symbol, queue),
                name=f"ws_sender_{symbol}",
            )
            sender_tasks.append(task)

        # 3) Mantener la conexión abierta
        #    (por ahora solo escuchamos texto para no cortar; se puede extender
        #     para soportar nuevos "subscribe"/"unsubscribe" dinámicos)
        while True:
            _ = await websocket.receive_text()
            # podrías soportar mensajes tipo:
            # { "type": "ping" } o nuevos "subscribe"/"unsubscribe" aquí

    except WebSocketDisconnect:
        # cliente cerró la conexión
        pass
    finally:
        # 4) Limpieza: desuscribir y cancelar tasks
        for symbol, queue in queues_by_symbol.items():
            await unsubscribe_quotes(symbol, queue)

        for t in sender_tasks:
            t.cancel()
        for t in sender_tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
