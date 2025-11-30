# app/main.py
from fastapi import FastAPI

from app.api.routes_account import router as account_router
from app.api.routes_bots import router as bots_router
from app.api.routes_manual import router as manual_router
from app.api.routes_trades import router as trades_router

from app.db import test_db_connection, get_running_bots
from app.bots.manager import ensure_bot_running_from_db_record


app = FastAPI(title="cTrader Bot API")


@app.on_event("startup")
async def startup_event():
    # 1) Test DB
    test_db_connection()

    # 2) Levantar bots que están marcados como RUNNING en la DB
    bots = get_running_bots()
    for bot in bots:
        ensure_bot_running_from_db_record(bot)


# Incluir routers
app.include_router(account_router)
app.include_router(bots_router)
app.include_router(manual_router)
app.include_router(trades_router)
