# scripts/test_john_wicks.py

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

settings = get_settings()

# ---------------------------------------------------------
# CONFIGURACIÓN BÁSICA
# ---------------------------------------------------------

# CLI:
#   uv run scripts/test_john_wicks.py EURUSD H1 200 0.65
TARGET_SYMBOL_NAME = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
TIMEFRAME = sys.argv[2].upper() if len(sys.argv) > 2 else "H1"
CANDLES_COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 200
MIN_WICK_RATIO = float(sys.argv[4]) if len(sys.argv) > 4 else 0.65  # 65%

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
# LÓGICA JOHN WICKS
# ---------------------------------------------------------

def _classify_row_as_john_wick(
    row: pd.Series,
    min_wick_ratio: float,
) -> str:
    """
    Clasificación de John Wick en base SOLO a las colas, no al color del cuerpo.

    - bullish: cola INFERIOR grande (lower_wick dominante)
    - bearish: cola SUPERIOR grande (upper_wick dominante)
    - none: no es JW

    Una vela verde con mecha superior enorme puede ser JW bajista,
    y una vela roja con mecha inferior enorme puede ser JW alcista.
    """
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])

    rango_total = h - l
    if rango_total <= 0:
        return "none"

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Requisitos:
    # - la wick debe ser al menos min_wick_ratio * rango_total
    # - y además ser la wick dominante
    threshold = min_wick_ratio * rango_total

    if upper_wick >= threshold and upper_wick > lower_wick:
        return "bearish"

    if lower_wick >= threshold and lower_wick > upper_wick:
        return "bullish"

    return "none"


def classify_john_wicks(
    df: pd.DataFrame,
    min_wick_ratio: float,
) -> pd.DataFrame:
    """
    Agrega columnas:
    - rango_total
    - upper_wick
    - lower_wick
    - direction (color de la vela normal, solo informativo)
    - john_wick_type (bullish / bearish / none)
    """
    df = df.copy()

    df["rango_total"] = df["high"] - df["low"]

    body_max = df[["open", "close"]].max(axis=1)
    body_min = df[["open", "close"]].min(axis=1)

    df["upper_wick"] = df["high"] - body_max
    df["lower_wick"] = body_min - df["low"]

    def _direction(row: pd.Series) -> str:
        if row["close"] > row["open"]:
            return "bullish"
        elif row["close"] < row["open"]:
            return "bearish"
        return "doji"

    df["direction"] = df.apply(_direction, axis=1)

    df["john_wick_type"] = df.apply(
        _classify_row_as_john_wick,
        axis=1,
        min_wick_ratio=min_wick_ratio,
    )

    return df


# ---------------------------------------------------------
# HANDLER DE RESULTADO DE VELAS → DF → JOHN WICKS
# ---------------------------------------------------------

def on_trendbars_res(message):
    res = Protobuf.extract(message)  # ProtoOAGetTrendbarsRes
    trendbars = list(res.trendbar)

    if not trendbars:
        print("⚠️ No se recibieron velas en la respuesta")
        reactor.stop()
        return

    print(f"\n✅ Recibidas {len(trendbars)} velas para {TARGET_SYMBOL_NAME} [{TIMEFRAME}]")

    # ---------- helpers ----------

    def get_tb_timestamp_ms(tb):
        # Versión vieja: ctm en milisegundos
        if hasattr(tb, "ctm"):
            return tb.ctm

        # Versión actual/documentada: utcTimestampInMinutes (epoch en MINUTOS)
        if hasattr(tb, "utcTimestampInMinutes"):
            return tb.utcTimestampInMinutes * 60_000

        # Fallback
        return 0

    # Ordenamos por timestamp
    trendbars.sort(key=get_tb_timestamp_ms)

    rows = []

    for tb in trendbars[-CANDLES_COUNT:]:
        ts_ms = get_tb_timestamp_ms(tb)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)

        # Reconstruimos OHLC desde ProtoOATrendbar:
        # low        -> base
        # deltaOpen  -> open  = low + deltaOpen
        # deltaClose -> close = low + deltaClose
        # deltaHigh  -> high  = low + deltaHigh
        low_raw = getattr(tb, "low", 0)
        d_open = getattr(tb, "deltaOpen", 0)
        d_close = getattr(tb, "deltaClose", 0)
        d_high = getattr(tb, "deltaHigh", 0)

        open_raw = low_raw + d_open
        close_raw = low_raw + d_close
        high_raw = low_raw + d_high

        SCALE = 1e5  # para FX típico
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

    df = pd.DataFrame(rows).set_index("time")

    print(f"   Tenemos {len(df)} velas en el DataFrame")

    if df.empty:
        print("⚠️ DataFrame vacío, no se puede buscar John Wicks")
        print("\n🏁 Fin del script, cerrando reactor...")
        reactor.stop()
        return

    # Clasificar John Wicks
    df_jw = classify_john_wicks(df, min_wick_ratio=MIN_WICK_RATIO)
    jw_only = df_jw[df_jw["john_wick_type"] != "none"].copy()

    total_jw = len(jw_only)
    bullish_jw = (jw_only["john_wick_type"] == "bullish").sum()
    bearish_jw = (jw_only["john_wick_type"] == "bearish").sum()

    print(
        f"\n🔍 John Wicks encontrados en las últimas {CANDLES_COUNT} velas "
        f"({TARGET_SYMBOL_NAME} {TIMEFRAME}): {total_jw}"
    )
    print(f"   - Bullish: {bullish_jw}")
    print(f"   - Bearish: {bearish_jw}")

    if total_jw == 0:
        print("\nNo se encontraron John Wicks con los parámetros actuales.")
        print("\n🏁 Fin del script, cerrando reactor...")
        reactor.stop()
        return

    print("\n📋 Detalle de John Wicks (orden cronológico):\n")

    for idx, row in jw_only.iterrows():
        ts = idx.isoformat()
        o = row["open"]
        h = row["high"]
        l = row["low"]
        c = row["close"]
        jw_type = row["john_wick_type"]
        direction = row["direction"]
        upper_wick = row["upper_wick"]
        lower_wick = row["lower_wick"]
        rango_total = row["rango_total"]

        print(
            f"{ts}  "
            f"O={o:.5f} H={h:.5f} L={l:.5f} C={c:.5f}  "
            f"dir={direction:<7}  JW={jw_type.upper():<7}  "
            f"rango={rango_total:.5f}  upper={upper_wick:.5f}  lower={lower_wick:.5f}"
        )

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
    req.period = period_code
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
    print("🔌 Conectado al proxy DEMO (historical / john wicks)")

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
        f"Iniciando test de John Wicks: symbol={TARGET_SYMBOL_NAME}, "
        f"tf={TIMEFRAME}, count={CANDLES_COUNT}, "
        f"min_wick_ratio={MIN_WICK_RATIO}"
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
