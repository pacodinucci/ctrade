# app/bots/manager.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import threading
import uuid
from datetime import datetime

from app.trading.bot_runner import JohnWickBot


@dataclass
class BotState:
    id: str
    instrument: str
    bot: JohnWickBot
    created_at: datetime


_bots: Dict[str, BotState] = {}
_lock = threading.Lock()


def start_bot(
    instrument: str,
    trend_tf: str = "M30",
    jw_tf: str = "M5",
    bot_id: Optional[str] = None,
) -> BotState:
    """
    Arranca un bot en memoria (thread) para el instrumento dado.
    - Si bot_id es None, genera un UUID nuevo.
    - Si ya existe un bot con ese bot_id y está corriendo, lo reutiliza.
    """
    with _lock:
        if bot_id is None:
            bot_id = str(uuid.uuid4())

        existing = _bots.get(bot_id)
        if existing and existing.bot.is_running():
            # Ya hay un bot con ese ID corriendo
            return existing

        bot = JohnWickBot(
            bot_id=bot_id,
            instrument=instrument,
            trend_tf=trend_tf,
            jw_tf=jw_tf,
        )
        bot.start()

        state = BotState(
            id=bot_id,
            instrument=instrument,
            bot=bot,
            created_at=datetime.utcnow(),
        )
        _bots[bot_id] = state
        return state


def ensure_bot_running_from_db_record(bot: dict) -> BotState:
    """
    Asegura que exista y esté corriendo un bot en memoria
    a partir de un registro de la DB.

    Espera un dict con:
      - bot["id"]          -> String (id en tu DB)
      - bot["instrument"]  -> String
      - bot["trend_tf"]    -> String (ej: "M30")
      - bot["jw_tf"]       -> String (ej: "M5")
    """
    return start_bot(
        instrument=bot["instrument"],
        trend_tf=bot["trend_tf"],
        jw_tf=bot["jw_tf"],
        bot_id=str(bot["id"]),
    )


def stop_bot(bot_id: str) -> Optional[BotState]:
    """
    Detiene un bot existente (no lo borra del registry).
    """
    with _lock:
        state = _bots.get(bot_id)
        if not state:
            return None
        state.bot.stop()
        return state


def get_bot(bot_id: str) -> Optional[BotState]:
    with _lock:
        return _bots.get(bot_id)


def list_bots() -> List[BotState]:
    with _lock:
        return list(_bots.values())


def remove_bot(bot_id: str) -> bool:
    """
    Lo saca del registry (primero lo frena).
    """
    with _lock:
        state = _bots.get(bot_id)
        if not state:
            return False
        state.bot.stop()
        del _bots[bot_id]
        return True
