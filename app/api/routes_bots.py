# app/api/routes_bots.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.bots.manager import start_bot, stop_bot, list_bots

router = APIRouter(prefix="/bots", tags=["bots"])


class CreateBotRequest(BaseModel):
    instrument: str
    trend_tf: str = "M30"
    jw_tf: str = "M5"


class BotInfo(BaseModel):
    id: str
    instrument: str
    trend_tf: str
    jw_tf: str
    running: bool


@router.post("", response_model=BotInfo)
def create_bot(payload: CreateBotRequest):
    """
    Crea un nuevo bot en memoria y lo arranca.
    """
    state = start_bot(
        instrument=payload.instrument,
        trend_tf=payload.trend_tf,
        jw_tf=payload.jw_tf,
    )
    return BotInfo(
        id=state.id,
        instrument=state.instrument,
        trend_tf=state.bot.trend_tf,
        jw_tf=state.bot.jw_tf,
        running=state.bot.is_running(),
    )


@router.get("", response_model=list[BotInfo])
def get_bots():
    """
    Devuelve el listado de bots en memoria.
    """
    states = list_bots()
    return [
        BotInfo(
            id=s.id,
            instrument=s.instrument,
            trend_tf=s.bot.trend_tf,
            jw_tf=s.bot.jw_tf,
            running=s.bot.is_running(),
        )
        for s in states
    ]


@router.delete("/{bot_id}", response_model=BotInfo)
def delete_bot(bot_id: str):
    """
    Detiene un bot (no lo borra de la DB, solo del registry en memoria).
    """
    state = stop_bot(bot_id)
    if not state:
        raise HTTPException(status_code=404, detail="Bot not found")

    return BotInfo(
        id=state.id,
        instrument=state.instrument,
        trend_tf=state.bot.trend_tf,
        jw_tf=state.bot.jw_tf,
        running=state.bot.is_running(),
    )
