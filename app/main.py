# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_account import router as account_router
from app.api.routes_bots import router as bots_router
from app.api.routes_manual import router as manual_router
from app.api.routes_trades import router as trades_router
from app.api.routes_test_watcher import router as test_watcher_router
from app.api.routes_ws_prices import router as ws_prices_router
from app.api.routes_history import router as history_router
from app.api.routes_backtest import router as backtest_router

from app.db import test_db_connection, get_running_bots
from app.bots.manager import ensure_bot_running_from_db_record


app = FastAPI(title="cTrader Bot API")

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    # si más adelante usás otra URL, la agregás acá
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    # 1) Test DB
    test_db_connection()

    # 2) Levantar bots que están marcados como RUNNING en la DB
    # bots = get_running_bots()
    # for bot in bots:
    #     ensure_bot_running_from_db_record(bot)


# Incluir routers
app.include_router(account_router)
app.include_router(bots_router)
app.include_router(manual_router)
app.include_router(trades_router)
app.include_router(test_watcher_router)
app.include_router(ws_prices_router)
app.include_router(history_router)
app.include_router(backtest_router)
