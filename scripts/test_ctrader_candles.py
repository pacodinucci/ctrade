# scripts/test_ctrader_candles.py

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg
from twisted.internet import reactor

from app.config import get_settings

settings = get_settings()

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# ---------------------------------------------------------

# Se pueden sobreescribir por CLI:
#   uv run scripts/test_ctrader_candles.py EURUSD M30 200
TARGET_SYMBOL_NAME = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
TIMEFRAME = sys.argv[2].upper() if len(sys.argv) > 2 else "M30"
CANDLES_COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 200

# 👇 IMPORTANTE: este es el ctidTraderAccountId (accountId en el JSON),
# NO el accountNumber.
CTID_TRADER_ACCOUNT_ID = 45440970  # <-- poné acá el "accountId" de tu JSON


def on_error(failure):
    print("❌ Error en mensaje (Deferred):", failure)
    reactor.stop()


# ---------------------------------------------------------
# MAPEO DE TIMEFRAMES
# ---------------------------------------------------------
# Usamos directamente los códigos numéricos del enum ProtoOATrendbarPeriod:
# M1=1, M2=2, M3=3, M4=4, M5=5, M10=6, M15=7, M30=8, H1=9, H4=10, H12=11,
# D1=12, W1=13, MN1=14
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

# Duración aproximada de cada vela en milisegundos
TF_MS = {
    "M1": 60_000,
    "M2": 2 * 60_000,
    "M3": 3 * 60_000,
    "M4": 4 * 60_000,
    "M5": 5 * 60_000,
    "M10": 10 * 60_000,
    "M15": 15 * 60_000,
    "M30": 30 * 60_000,
    "H1": 60 * 60_000,
    "H4": 4 * 60 * 60_000,
    "H12": 12 * 60 * 60_000,
    "D1": 24 * 60 * 60_000,
    "W1": 7 * 24 * 60 * 60_000,
    "MN1": 30 * 24 * 60 * 60_000,  # aprox
}


# ---------------------------------------------------------
# HANDLER DE RESULTADO DE VELAS
# ---------------------------------------------------------

def on_trendbars_res(message):
    res = Protobuf.extract(message)  # ProtoOAGetTrendbarsRes
    trendbars = list(res.trendbar)

    if not trendbars:
        print("⚠️ No se recibieron velas en la respuesta")
        reactor.stop()
        return

    print(f"\n✅ Recibidas {len(trendbars)} velas para {TARGET_SYMBOL_NAME} [{TIMEFRAME}]")
    print("   (mostrando en orden cronológico)\n")

    # ---------- helpers ----------

    def get_tb_timestamp_ms(tb):
        # Versión vieja: ctm en milisegundos
        if hasattr(tb, "ctm"):
            return tb.ctm

        # Versión actual/documentada: utcTimestampInMinutes (epoch en MINUTOS)
        if hasattr(tb, "utcTimestampInMinutes"):
            return tb.utcTimestampInMinutes * 60_000

        # Fallback por si acaso
        return 0

    def decode_ohlc(tb):
        """
        ProtoOATrendbar:
          low           -> precio base (int)
          deltaOpen     -> open = low + deltaOpen
          deltaClose    -> close = low + deltaClose
          deltaHigh     -> high = low + deltaHigh

        Los precios están en unidades 'precio * 10^5' (para FX típico),
        así que dividimos por 1e5 para verlo en formato normal.
        """
        low_raw = getattr(tb, "low", 0)
        d_open = getattr(tb, "deltaOpen", 0)
        d_close = getattr(tb, "deltaClose", 0)
        d_high = getattr(tb, "deltaHigh", 0)

        open_raw = low_raw + d_open
        close_raw = low_raw + d_close
        high_raw = low_raw + d_high

        SCALE = 1e5  # suposición estándar FX; luego podemos usar digits del símbolo

        low = low_raw / SCALE
        o = open_raw / SCALE
        h = high_raw / SCALE
        c = close_raw / SCALE

        return o, h, low / SCALE * SCALE / SCALE if False else low, c  # placeholder

    # Ordenamos por timestamp
    trendbars.sort(key=get_tb_timestamp_ms)

    for tb in trendbars[-CANDLES_COUNT:]:
        ts_ms = get_tb_timestamp_ms(tb)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)

        # reconstruimos OHLC
        low_raw = getattr(tb, "low", 0)
        d_open = getattr(tb, "deltaOpen", 0)
        d_close = getattr(tb, "deltaClose", 0)
        d_high = getattr(tb, "deltaHigh", 0)

        open_raw = low_raw + d_open
        close_raw = low_raw + d_close
        high_raw = low_raw + d_high

        SCALE = 1e5  # para EURUSD demo está perfecto
        low = low_raw / SCALE
        o = open_raw / SCALE
        h = high_raw / SCALE
        c = close_raw / SCALE

        v = getattr(tb, "volume", None)

        if v is not None:
            print(f"{dt.isoformat()}  O={o} H={h} L={low} C={c} Vol={v}")
        else:
            print(f"{dt.isoformat()}  O={o} H={h} L={low} C={c}")

    print("\n🏁 Fin del script, cerrando reactor...")
    reactor.stop()


# ---------------------------------------------------------
# SOLICITUD DE VELAS
# ---------------------------------------------------------

def request_trendbars(client: Client, symbol_id: int):
    tf = TIMEFRAME.upper()
    if tf not in TF_CODE or tf not in TF_MS:
        print(f"❌ Timeframe no soportado: {tf}")
        print(f"   Soportados: {', '.join(sorted(TF_CODE.keys()))}")
        reactor.stop()
        return

    period_code = TF_CODE[tf]
    period_ms = TF_MS[tf]

    now_ms = int(time.time() * 1000)
    from_ms = now_ms - CANDLES_COUNT * period_ms

    print(
        f"\n📡 Pidiendo últimas {CANDLES_COUNT} velas de "
        f"{TARGET_SYMBOL_NAME} [{tf}] "
        f"desde {datetime.fromtimestamp(from_ms/1000, tz=timezone.utc).isoformat()} "
        f"hasta {datetime.fromtimestamp(now_ms/1000, tz=timezone.utc).isoformat()}\n"
    )

    req = OAMsg.ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.symbolId = symbol_id
    req.period = period_code  # 👈 acá usamos el int, no el enum
    req.fromTimestamp = from_ms
    req.toTimestamp = now_ms

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

        if name == TARGET_SYMBOL_NAME:
            target_id = sid
            break

    if target_id is None:
        print(f"⚠️ No encontré {TARGET_SYMBOL_NAME} en la lista de símbolos")
        reactor.stop()
        return

    print(f"✅ Encontrado {TARGET_SYMBOL_NAME} con symbolId={target_id}")
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

    # Ahora autenticamos la CUENTA
    req = OAMsg.ProtoOAAccountAuthReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.accessToken = settings.CTRADER_ACCESS_TOKEN

    d = client.send(req)
    d.addErrback(on_error)


def on_connected(client: Client):
    print("🔌 Conectado al proxy DEMO (historical / candles)")

    # 1) Auth de la APLICACIÓN
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
        f"Iniciando test de velas: symbol={TARGET_SYMBOL_NAME}, "
        f"tf={TIMEFRAME}, count={CANDLES_COUNT}"
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
