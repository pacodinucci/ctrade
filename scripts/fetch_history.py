# scripts/fetch_history.py

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg
from twisted.internet import reactor

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

settings = get_settings()

# ---------------------------------------------------------
# PARAMS CLI
# ---------------------------------------------------------
# Ejemplos:
#   uv run scripts/fetch_history.py EURUSD H1 2025-11-01 2025-11-30
#
#   - símbolo       (default EURUSD)
#   - timeframe     (default H1)
#   - from_date     (default 2025-11-01)
#   - to_date       (default 2025-11-30)

SYMBOL_NAME = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
TIMEFRAME = sys.argv[2].upper() if len(sys.argv) > 2 else "H1"
FROM_DATE_STR = sys.argv[3] if len(sys.argv) > 3 else "2025-11-01"
TO_DATE_STR = sys.argv[4] if len(sys.argv) > 4 else "2025-11-30"

# 👇 IMPORTANTE: este es el ctidTraderAccountId (accountId en el JSON),
# NO el accountNumber.
CTID_TRADER_ACCOUNT_ID = 45440970  # <-- cambiá por tu accountId

# ---------------------------------------------------------
# MAPEO DE TIMEFRAMES
# ---------------------------------------------------------
TF_CODE = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M10": 6,
    "M15": 7,
    "M30": 8,
    "H1": 9,
    "H4": 10,
    "H12": 11,
    "D1": 12,
    "W1": 13,
    "MN1": 14,
}


def on_error(failure):
    print("❌ Error en mensaje (Deferred):", failure)
    reactor.stop()


# ---------------------------------------------------------
# HANDLER DE VELAS → DF → ARCHIVO
# ---------------------------------------------------------

def on_trendbars_res(message):
    res = Protobuf.extract(message)  # ProtoOAGetTrendbarsRes
    trendbars = list(res.trendbar)

    if not trendbars:
        print("⚠️ No se recibieron velas en la respuesta")
        reactor.stop()
        return

    print(f"\n✅ Recibidas {len(trendbars)} velas para {SYMBOL_NAME} [{TIMEFRAME}]")

    def get_tb_timestamp_ms(tb):
        if hasattr(tb, "ctm"):
            return tb.ctm
        if hasattr(tb, "utcTimestampInMinutes"):
            return tb.utcTimestampInMinutes * 60_000
        return 0

    trendbars.sort(key=get_tb_timestamp_ms)

    rows = []

    for tb in trendbars:
        ts_ms = get_tb_timestamp_ms(tb)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)

        low_raw = getattr(tb, "low", 0)
        d_open = getattr(tb, "deltaOpen", 0)
        d_close = getattr(tb, "deltaClose", 0)
        d_high = getattr(tb, "deltaHigh", 0)

        open_raw = low_raw + d_open
        close_raw = low_raw + d_close
        high_raw = low_raw + d_high

        SCALE = 1e5  # FX típico 5 decimales
        low = low_raw / SCALE
        o = open_raw / SCALE
        h = high_raw / SCALE
        c = close_raw / SCALE

        v = getattr(tb, "volume", 0)

        rows.append(
            {
                "time": dt,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": v,
            }
        )

    df = pd.DataFrame(rows).set_index("time").sort_index()

    print(f"   Tenemos {len(df)} velas en el DataFrame")

    # Ruta de salida: data/EURUSD_H1_2025-11-01_2025-11-30.parquet
    out_name = f"{SYMBOL_NAME}_{TIMEFRAME}_{FROM_DATE_STR}_{TO_DATE_STR}.parquet"
    out_path = DATA_DIR / out_name

    df.to_parquet(out_path)
    print(f"\n💾 Histórico guardado en: {out_path}")

    print("\n🏁 Fin del script, cerrando reactor...")
    reactor.stop()


# ---------------------------------------------------------
# SOLICITUD DE VELAS
# ---------------------------------------------------------

def request_trendbars(client: Client, symbol_id: int):
    tf = TIMEFRAME.upper()
    if tf not in TF_CODE:
        print(f"❌ Timeframe no soportado: {tf}")
        print(f"   Soportados: {', '.join(sorted(TF_CODE.keys()))}")
        reactor.stop()
        return

    period_code = TF_CODE[tf]

    # Fechas desde CLI → timestamps en ms (UTC)
    from_dt = datetime.fromisoformat(FROM_DATE_STR).replace(tzinfo=timezone.utc)
    # sumamos casi un día para incluir la fecha final completa
    to_dt = datetime.fromisoformat(TO_DATE_STR).replace(tzinfo=timezone.utc)
    # cTrader acepta [from, to]; usamos fin del día:
    to_dt = to_dt.replace(hour=23, minute=59, second=59, microsecond=0)

    from_ms = int(from_dt.timestamp() * 1000)
    to_ms = int(to_dt.timestamp() * 1000)

    print(
        f"\n📡 Pidiendo velas de {SYMBOL_NAME} [{tf}] "
        f"desde {from_dt.isoformat()} hasta {to_dt.isoformat()}\n"
    )

    req = OAMsg.ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.symbolId = symbol_id
    req.period = period_code
    req.fromTimestamp = from_ms
    req.toTimestamp = to_ms

    d = client.send(req)
    d.addErrback(on_error)


# ---------------------------------------------------------
# SYMBOL LIST FLOW
# ---------------------------------------------------------

def on_symbols_list_res(message, client: Client):
    res = Protobuf.extract(message)  # ProtoOASymbolsListRes

    target_id = None

    print("\n=== Buscando símbolo en la lista recibida ===")
    for s in res.symbol:
        name = getattr(s, "symbolName", None)
        sid = getattr(s, "symbolId", None)
        if name is None or sid is None:
            continue

        if name == SYMBOL_NAME:
            target_id = sid
            break

    if target_id is None:
        print(f"⚠️ No encontré {SYMBOL_NAME} en la lista de símbolos")
        reactor.stop()
        return

    print(f"✅ Encontrado {SYMBOL_NAME} con symbolId={target_id}")
    request_trendbars(client, target_id)


# ---------------------------------------------------------
# AUTH FLOWS
# ---------------------------------------------------------

def on_account_auth_res(message, client: Client):
    print("✅ Account AUTH OK, pidiendo lista de símbolos...")

    req = OAMsg.ProtoOASymbolsListReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID

    d = client.send(req)
    d.addErrback(on_error)


def on_app_auth_res(message, client: Client):
    print("✅ Application AUTH OK")

    req = OAMsg.ProtoOAAccountAuthReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.accessToken = settings.CTRADER_ACCESS_TOKEN

    d = client.send(req)
    d.addErrback(on_error)


def on_connected(client: Client):
    print("🔌 Conectado al proxy DEMO (fetch_history)")

    req = OAMsg.ProtoOAApplicationAuthReq()
    req.clientId = settings.CTRADER_CLIENT_ID
    req.clientSecret = settings.CTRADER_CLIENT_SECRET

    d = client.send(req)
    d.addErrback(on_error)


def on_disconnected(client, reason):
    print("🔌 Desconectado:", reason)


def on_message_received(client: Client, message):
    decoded = Protobuf.extract(message)
    msg_type = type(decoded).__name__
    print("📩 Mensaje recibido tipo:", msg_type)

    if isinstance(decoded, OAMsg.ProtoOAApplicationAuthRes):
        on_app_auth_res(message, client)
    elif isinstance(decoded, OAMsg.ProtoOAAccountAuthRes):
        on_account_auth_res(message, client)
    elif isinstance(decoded, OAMsg.ProtoOASymbolsListRes):
        on_symbols_list_res(message, client)
    elif isinstance(decoded, OAMsg.ProtoOAGetTrendbarsRes):
        on_trendbars_res(message)
    elif isinstance(decoded, OAMsg.ProtoOAErrorRes):
        print("❌ ProtoOAErrorRes recibido:")
        print(decoded)
        reactor.stop()


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    print(
        f"Iniciando fetch de histórico:"
        f" symbol={SYMBOL_NAME}, tf={TIMEFRAME},"
        f" from={FROM_DATE_STR}, to={TO_DATE_STR}"
    )

    client = Client(
        EndPoints.PROTOBUF_DEMO_HOST,
        EndPoints.PROTOBUF_PORT,
        TcpProtocol,
    )

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(on_disconnected)
    client.setMessageReceivedCallback(on_message_received)

    client.startService()
    reactor.run()


if __name__ == "__main__":
    main()
