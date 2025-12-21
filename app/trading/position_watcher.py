from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict

from app.broker.ctrader_market_data import (
    subscribe_quotes,
    unsubscribe_quotes,
    close_position as md_close_position,
    Quote,
)


@dataclass
class PositionWatchConfig:
    # distancia en precio (por ej. 0.0001 para EURUSD = 10 “puntos” en 5 dígitos)
    max_move_price: float


@dataclass
class PositionWatchState:
    position_id: int
    symbol: str
    side: str        # "buy" | "sell"
    entry_price: float
    config: PositionWatchConfig
    queue: asyncio.Queue[Quote]
    task: asyncio.Task


class PositionWatcherManager:
    def __init__(self) -> None:
        # key = position_id
        self._watchers: Dict[int, PositionWatchState] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        position_id: int,
        symbol: str,
        side: str,
        entry_price: float,
        config: PositionWatchConfig,
    ) -> None:
        symbol_u = symbol.upper()

        async with self._lock:
            # si ya hubiera un watcher para esa posición, lo apagamos
            if position_id in self._watchers:
                await self.stop(position_id)

            queue: asyncio.Queue[Quote] = await subscribe_quotes(symbol_u)

            state = PositionWatchState(
                position_id=position_id,
                symbol=symbol_u,
                side=side.lower(),
                entry_price=float(entry_price),
                config=config,
                queue=queue,
                task=None,  # lo seteamos abajo
            )

            task = asyncio.create_task(
                self._run_watcher(state),
                name=f"position_watcher_{position_id}",
            )
            state.task = task
            self._watchers[position_id] = state

        print(
            f"[WATCHER] Iniciado para position_id={position_id} "
            f"symbol={symbol_u} side={side} entry={entry_price} "
            f"max_move={config.max_move_price}"
        )

    async def stop(self, position_id: int) -> None:
        async with self._lock:
            state = self._watchers.pop(position_id, None)

        if not state:
            return

        print(f"[WATCHER] Cancelando watcher de position_id={position_id}")
        state.task.cancel()
        try:
            await state.task
        except asyncio.CancelledError:
            pass

        await unsubscribe_quotes(state.symbol, state.queue)

    async def _run_watcher(self, state: PositionWatchState) -> None:
        """
        Loop que escucha quotes y, cuando el precio se mueve max_move_price
        a favor o en contra, cierra la posición y se apaga.
        """
        try:
            while True:
                quote = await state.queue.get()

                # 🔴 Ignorar ticks inválidos (cuando se va a 0 por desconexión/reconnect, etc.)
                if quote.bid <= 0 or quote.ask <= 0:
                    print(
                        f"[WATCHER {state.position_id}] Tick inválido "
                        f"bid={quote.bid} ask={quote.ask}, se ignora"
                    )
                    continue

                # Para BUY usamos BID (precio al que podemos vender),
                # para SELL usamos ASK (precio al que podemos recomprar).
                if state.side == "buy":
                    price = quote.bid
                else:
                    price = quote.ask

                move = price - state.entry_price
                # distancia absoluta en precio
                dist = abs(move)

                print(
                    f"[WATCHER {state.position_id}] "
                    f"price={price:.5f} entry={state.entry_price:.5f} "
                    f"move={move:+.5f} dist={dist:.5f}"
                )

                if dist >= state.config.max_move_price:
                    print(
                        f"[WATCHER {state.position_id}] "
                        f"🔔 Umbral alcanzado (dist={dist:.5f}), cerrando posición..."
                    )
                    await md_close_position(state.position_id, None)
                    break

        except asyncio.CancelledError:
            print(f"[WATCHER {state.position_id}] Cancelado")
            raise
        except Exception as e:
            print(f"[WATCHER {state.position_id}] ERROR: {e!r}")
        finally:
            # limpieza
            await unsubscribe_quotes(state.symbol, state.queue)
            async with self._lock:
                self._watchers.pop(state.position_id, None)
            print(f"[WATCHER {state.position_id}] Finalizado")


# singleton global
position_watcher_manager = PositionWatcherManager()
