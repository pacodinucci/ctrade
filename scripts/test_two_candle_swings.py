# scripts/test_two_candle_swings.py

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------
# PATH SETUP
# ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------
# cTrader imports
# ---------------------------------------------------------

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg
from twisted.internet import reactor

from app.config import get_settings
from app.trading.highs_and_lows_indetify import find_last_5_highs_lows_two_candle

# ✅ IMPORTAR LA FUNCIÓN NUEVA (FLEXIBLE) DESDE trading/H4_high_lows_bias.py
#    Asegurate de exponerla con este nombre en ese archivo.
from app.trading.H4_high_lows_bias import determine_trend_bias_from_swings_flexible

settings = get_settings()

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

TARGET_SYMBOL_NAME = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"

TIMEFRAME = "H4"
CANDLES_COUNT = 600

# reglas del algoritmo (las mismas que tu función de swings)
DOJI_POINTS = 10          # en POINTS
MERGE_POINTS = 30         # en POINTS

# para bias
BIAS_EPS_POINTS = 30      # tolerancia para considerar "sube/baja"
MIN_POINTS_FOR_BIAS = 5   # usar los últimos 5 swings

# FX 5 dígitos (ajustar si JPY)
POINT_SIZE = 0.00001

CTID_TRADER_ACCOUNT_ID = 45440970  # accountId (NO accountNumber)

# ---------------------------------------------------------
# TIMEFRAME MAP
# ---------------------------------------------------------

TF_CODE = {"H4": 10}
TF_MS = {"H4": 4 * 60 * 60_000}

# ---------------------------------------------------------
# RESPONSE HANDLER
# ---------------------------------------------------------

def on_trendbars_res(message):
    res = Protobuf.extract(message)  # ProtoOAGetTrendbarsRes
    trendbars = list(res.trendbar)

    if not trendbars:
        print("❌ No se recibieron velas")
        reactor.stop()
        return

    def get_tb_timestamp_ms(tb):
        # compatibilidad: algunos entornos traen ctm, otros utcTimestampInMinutes
        if hasattr(tb, "ctm"):
            return tb.ctm
        if hasattr(tb, "utcTimestampInMinutes"):
            return tb.utcTimestampInMinutes * 60_000
        raise RuntimeError("Trendbar sin timestamp conocido (ctm/utcTimestampInMinutes)")

    trendbars.sort(key=get_tb_timestamp_ms)

    rows = []
    for tb in trendbars[-CANDLES_COUNT:]:
        ts_ms = get_tb_timestamp_ms(tb)
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        low_raw = tb.low
        open_raw = tb.low + tb.deltaOpen
        high_raw = tb.low + tb.deltaHigh
        close_raw = tb.low + tb.deltaClose

        SCALE = 1e5  # FX típico
        rows.append(
            {
                "time": ts,
                "open": open_raw / SCALE,
                "high": high_raw / SCALE,
                "low": low_raw / SCALE,
                "close": close_raw / SCALE,
            }
        )

    df = pd.DataFrame(rows).set_index("time")

    print(
        f"\n✅ DataFrame armado:"
        f"\n   symbol={TARGET_SYMBOL_NAME}"
        f"\n   timeframe={TIMEFRAME}"
        f"\n   candles={len(df)}"
        f"\n   from={df.index.min().isoformat()}"
        f"\n   to  ={df.index.max().isoformat()}"
    )

    # ---------------------------------------------------------
    # 1) DETECTAR SWINGS
    # ---------------------------------------------------------

    doji_price = DOJI_POINTS * POINT_SIZE
    merge_price = MERGE_POINTS * POINT_SIZE

    highs, lows = find_last_5_highs_lows_two_candle(
        df_h4=df,
        doji_points=doji_price,
        k=5,
        merge_threshold_points=merge_price,
    )

    print("\n🟠 HIGHS (last 5, merged)")
    if not highs:
        print("  (ninguno)")
    for h in highs:
        print(f"  ts={h.ts.isoformat()}  level={h.level:.5f}  (i1={h.i1}, i2={h.i2})")

    print("\n🔵 LOWS (last 5, merged)")
    if not lows:
        print("  (ninguno)")
    for l in lows:
        print(f"  ts={l.ts.isoformat()}  level={l.level:.5f}  (i1={l.i1}, i2={l.i2})")

    # ---------------------------------------------------------
    # 2) TREND BIAS (IMPORTADO - FLEXIBLE)
    # ---------------------------------------------------------

    eps_price = BIAS_EPS_POINTS * POINT_SIZE

    # ✅ NUEVA FUNCIÓN FLEXIBLE
    bias = determine_trend_bias_from_swings_flexible(
        highs=highs,
        lows=lows,
        eps=eps_price,
        min_points=MIN_POINTS_FOR_BIAS,
        # opcionales (si querés tunear sin tocar el módulo):
        # threshold=0.8,
        # slope_weight=1.0,
        # violation_weight=0.35,
    )

    print("\n📈 TREND BIAS (H4 highs/lows) [FLEXIBLE]")
    print(f"  eps={BIAS_EPS_POINTS} points ({eps_price:.8f} price units)")
    print(f"  min_points={MIN_POINTS_FOR_BIAS}")
    print(f"  bias={bias.bias}")

    # soporte para ambos tipos de result (por si tu dataclass difiere)
    score = getattr(bias, "score", None)
    if score is not None:
        print(f"  score={score:.3f}")

    print(f"  highs_up={bias.highs_up} highs_down={bias.highs_down} highs_slope={bias.highs_slope:.10f}")
    print(f"  lows_up ={bias.lows_up}  lows_down ={bias.lows_down}  lows_slope ={bias.lows_slope:.10f}")
    print(f"  reason: {bias.reason}")

    print("\n🏁 Fin del test, cerrando reactor...")
    reactor.stop()

# ---------------------------------------------------------
# REQUEST FLOW
# ---------------------------------------------------------

def request_trendbars(client: Client, symbol_id: int):
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - CANDLES_COUNT * TF_MS[TIMEFRAME]

    print(f"\n📡 Solicitando {CANDLES_COUNT} velas {TIMEFRAME} de {TARGET_SYMBOL_NAME}")

    req = OAMsg.ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.symbolId = symbol_id
    req.period = TF_CODE[TIMEFRAME]
    req.fromTimestamp = from_ms
    req.toTimestamp = now_ms

    client.send(req)

def on_symbols_list_res(message, client: Client):
    res = Protobuf.extract(message)

    for s in res.symbol:
        if s.symbolName == TARGET_SYMBOL_NAME:
            print(f"✅ Símbolo encontrado: {TARGET_SYMBOL_NAME} (id={s.symbolId})")
            request_trendbars(client, s.symbolId)
            return

    print(f"❌ Símbolo no encontrado: {TARGET_SYMBOL_NAME}")
    reactor.stop()

def on_account_auth_res(message, client: Client):
    req = OAMsg.ProtoOASymbolsListReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    client.send(req)

def on_app_auth_res(message, client: Client):
    req = OAMsg.ProtoOAAccountAuthReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.accessToken = settings.CTRADER_ACCESS_TOKEN
    client.send(req)

def on_connected(client: Client):
    print("🔌 Conectado a cTrader DEMO")

    req = OAMsg.ProtoOAApplicationAuthReq()
    req.clientId = settings.CTRADER_CLIENT_ID
    req.clientSecret = settings.CTRADER_CLIENT_SECRET
    client.send(req)

def on_message_received(client: Client, message):
    decoded = Protobuf.extract(message)

    if isinstance(decoded, OAMsg.ProtoOAApplicationAuthRes):
        on_app_auth_res(message, client)
    elif isinstance(decoded, OAMsg.ProtoOAAccountAuthRes):
        on_account_auth_res(message, client)
    elif isinstance(decoded, OAMsg.ProtoOASymbolsListRes):
        on_symbols_list_res(message, client)
    elif isinstance(decoded, OAMsg.ProtoOAGetTrendbarsRes):
        on_trendbars_res(message)
    elif isinstance(decoded, OAMsg.ProtoOAErrorRes):
        print("❌ Error ProtoOA:", decoded)
        reactor.stop()

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    print(
        f"▶ Test swings + trend bias (importado - FLEXIBLE)\n"
        f"  symbol={TARGET_SYMBOL_NAME}\n"
        f"  timeframe={TIMEFRAME}\n"
        f"  candles={CANDLES_COUNT}\n"
        f"  doji={DOJI_POINTS} points\n"
        f"  merge={MERGE_POINTS} points\n"
        f"  bias_eps={BIAS_EPS_POINTS} points\n"
        f"  bias_min_points={MIN_POINTS_FOR_BIAS}\n"
    )

    client = Client(
        EndPoints.PROTOBUF_DEMO_HOST,
        EndPoints.PROTOBUF_PORT,
        TcpProtocol,
    )

    client.setConnectedCallback(on_connected)
    client.setMessageReceivedCallback(on_message_received)

    client.startService()
    reactor.run()

if __name__ == "__main__":
    main()
