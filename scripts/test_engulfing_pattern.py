# scripts/test_engulfing_pattern.py

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
#   uv run scripts/test_engulfing_pattern.py EURUSD H1 200 1.0
TARGET_SYMBOL_NAME = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
TIMEFRAME = sys.argv[2].upper() if len(sys.argv) > 2 else "H1"
CANDLES_COUNT = int(sys.argv[3]) if len(sys.argv) > 3 else 200
# Mínimo tamaño relativo del cuerpo actual vs cuerpo anterior (1.0 = igual o mayor)
MIN_BODY_RATIO = float(sys.argv[4]) if len(sys.argv) > 4 else 0.8

# porcentaje mínimo de solapamiento del cuerpo previo que debe cubrir el cuerpo actual
ENGULFING_OVERLAP_RATIO = 0.8 

# el cuerpo de la vela previa debe ocupar al menos este porcentaje del rango total (high-low)
MIN_PREV_BODY_SHARE = 0.6

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
# LÓGICA DE ENGULFING PATTERN
# ---------------------------------------------------------

def _direction(o: float, c: float) -> str:
    if c > o:
        return "bullish"
    elif c < o:
        return "bearish"
    return "doji"


def _classify_row_as_engulfing(row: pd.Series, min_body_ratio: float) -> str:
    """
    Clasificación de patrón envolvente (engulfing) usando velas normales.

    Reglas:

    - Bullish engulfing:
        * vela anterior bajista
        * vela actual alcista
        * el cuerpo de la vela previa ocupa al menos MIN_PREV_BODY_SHARE del rango total
        * el cuerpo actual solapa al menos ENGULFING_OVERLAP_RATIO (80%)
          del cuerpo anterior
        * tamaño del cuerpo actual >= min_body_ratio * tamaño cuerpo anterior

    - Bearish engulfing:
        * vela anterior alcista
        * vela actual bajista
        * mismos criterios de cuerpo previo + solapamiento + tamaño
    """
    prev_o = row["prev_open"]
    prev_c = row["prev_close"]
    prev_h = row["prev_high"]
    prev_l = row["prev_low"]

    o = row["open"]
    c = row["close"]

    # Si no hay vela previa, no puede haber patrón
    if pd.isna(prev_o) or pd.isna(prev_c) or pd.isna(prev_h) or pd.isna(prev_l):
        return "none"

    prev_dir = _direction(prev_o, prev_c)
    curr_dir = _direction(o, c)

    prev_body_min = min(prev_o, prev_c)
    prev_body_max = max(prev_o, prev_c)
    curr_body_min = min(o, c)
    curr_body_max = max(o, c)

    prev_body_size = prev_body_max - prev_body_min
    curr_body_size = curr_body_max - curr_body_min

    if prev_body_size <= 0 or curr_body_size <= 0:
        return "none"

    # 1) la vela previa debe tener un cuerpo "grande" respecto a su rango
    prev_range = prev_h - prev_l
    if prev_range <= 0:
        return "none"

    prev_body_share = prev_body_size / prev_range
    if prev_body_share < MIN_PREV_BODY_SHARE:
        return "none"

    # 2) Cuerpo actual suficientemente grande vs cuerpo previo
    if curr_body_size < min_body_ratio * prev_body_size:
        return "none"

    # 3) Cálculo del solapamiento de cuerpos
    overlap_min = max(prev_body_min, curr_body_min)
    overlap_max = min(prev_body_max, curr_body_max)
    overlap = overlap_max - overlap_min

    if overlap <= 0:
        return "none"

    # Debe solapar al menos ENGULFING_OVERLAP_RATIO del cuerpo anterior
    if overlap < ENGULFING_OVERLAP_RATIO * prev_body_size:
        return "none"

    # 4) Clasificación según cambio de color
    if prev_dir == "bearish" and curr_dir == "bullish":
        return "bullish"

    if prev_dir == "bullish" and curr_dir == "bearish":
        return "bearish"

    return "none"


def classify_engulfing(df: pd.DataFrame, min_body_ratio: float) -> pd.DataFrame:
    """
    Agrega columnas:
    - direction (color de la vela actual)
    - prev_open, prev_close, prev_high, prev_low
    - prev_direction
    - engulfing_type: bullish / bearish / none (en la vela actual)
    """
    df = df.copy()

    df["direction"] = df.apply(lambda r: _direction(r["open"], r["close"]), axis=1)

    df["prev_open"] = df["open"].shift(1)
    df["prev_close"] = df["close"].shift(1)
    df["prev_high"] = df["high"].shift(1)
    df["prev_low"] = df["low"].shift(1)

    df["prev_direction"] = df.apply(
        lambda r: (
            _direction(r["prev_open"], r["prev_close"])
            if not pd.isna(r["prev_open"])
            else "none"
        ),
        axis=1,
    )

    df["engulfing_type"] = df.apply(
        _classify_row_as_engulfing,
        axis=1,
        min_body_ratio=min_body_ratio,
    )

    return df


# ---------------------------------------------------------
# HANDLER DE RESULTADO DE VELAS → DF → ENGULFING
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
        print("⚠️ DataFrame vacío, no se puede buscar engulfing patterns")
        print("\n🏁 Fin del script, cerrando reactor...")
        reactor.stop()
        return

    # Clasificar patrones engulfing
    df_eng = classify_engulfing(df, min_body_ratio=MIN_BODY_RATIO)
    engulf_only = df_eng[df_eng["engulfing_type"] != "none"].copy()

    total = len(engulf_only)
    bullish = (engulf_only["engulfing_type"] == "bullish").sum()
    bearish = (engulf_only["engulfing_type"] == "bearish").sum()

    print(
        f"\n🔍 Patrones ENGULFING encontrados en las últimas {CANDLES_COUNT} velas "
        f"({TARGET_SYMBOL_NAME} {TIMEFRAME}): {total}"
    )
    print(f"   - Bullish engulfing: {bullish}")
    print(f"   - Bearish engulfing: {bearish}")

    if total == 0:
        print("\nNo se encontraron patrones engulfing con los parámetros actuales.")
        print("\n🏁 Fin del script, cerrando reactor...")
        reactor.stop()
        return

    print("\n📋 Detalle de patrones ENGULFING (orden cronológico):\n")

    for idx, row in engulf_only.iterrows():
        ts = idx.isoformat()
        o = row["open"]
        h = row["high"]
        l = row["low"]
        c = row["close"]
        direction = row["direction"]
        etype = row["engulfing_type"]

        prev_o = row["prev_open"]
        prev_c = row["prev_close"]
        prev_dir = row["prev_direction"]

        curr_body = abs(c - o)
        prev_body = abs(prev_c - prev_o)

        print(
            f"{ts}  "
            f"[PREV: O={prev_o:.5f} C={prev_c:.5f} dir={prev_dir:<7}]  "
            f"[CURR: O={o:.5f} H={h:.5f} L={l:.5f} C={c:.5f} dir={direction:<7}]  "
            f"ENGULF={etype.upper():<7}  "
            f"body_prev={prev_body:.5f}  body_curr={curr_body:.5f}"
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
    print("🔌 Conectado al proxy DEMO (historical / engulfing pattern)")

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
        f"Iniciando test de ENGULFING: symbol={TARGET_SYMBOL_NAME}, "
        f"tf={TIMEFRAME}, count={CANDLES_COUNT}, "
        f"min_body_ratio={MIN_BODY_RATIO}, "
        f"overlap_ratio={ENGULFING_OVERLAP_RATIO}, "
        f"min_prev_body_share={MIN_PREV_BODY_SHARE}"
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
