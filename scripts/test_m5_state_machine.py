# scripts/test_m5_state_machine.py
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

# tus herramientas existentes
from app.trading.highs_and_lows_indetify import find_last_5_highs_lows_two_candle
from app.trading.H4_high_lows_bias import determine_trend_bias_from_swings_flexible

# NUEVO: state machine
from app.trading.m5_state_machine import DailyPlan, IntradayState, update_state_m5

settings = get_settings()

# ---------------------------------------------------------
# CONFIG (CLI)
# ---------------------------------------------------------
TARGET_SYMBOL_NAME = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"

CTID_TRADER_ACCOUNT_ID = 45440970  # accountId (NO accountNumber)

# Velas para H4 (swings+bias)
H4_CANDLES = 600

# Velas para D1 (close último D1)
D1_CANDLES = 10

# Velas para M5 (simular intradía)
M5_CANDLES = 450  # ~1.5 días

# Reglas swings (ya las tenés)
DOJI_POINTS = 10
MERGE_POINTS = 30

# Bias eps
BIAS_EPS_POINTS = 30
MIN_POINTS_FOR_BIAS = 5

# State machine params
ZONE_POINTS = 30
CLOSE_EPS_POINTS = 5
BREAK_EPS_POINTS = 5
MAX_WAIT_BARS = 120  # 120 velas M5 = 10 horas

# Point size: por ahora FX 5 dígitos. Si usás JPY => 0.001 / 0.01 según tu convención
POINT_SIZE = 0.00001

# ---------------------------------------------------------
# TIMEFRAME MAP
# ---------------------------------------------------------
TF_CODE = {
    "M5": 5,
    "H4": 10,
    "D1": 12,
}
TF_MS = {
    "M5": 5 * 60_000,
    "H4": 4 * 60 * 60_000,
    "D1": 24 * 60 * 60_000,
}

# ---------------------------------------------------------
# Trendbar timestamp compatibility
# ---------------------------------------------------------
def get_tb_timestamp_ms(tb) -> int:
    if hasattr(tb, "ctm"):
        return tb.ctm
    if hasattr(tb, "utcTimestampInMinutes"):
        return tb.utcTimestampInMinutes * 60_000
    raise RuntimeError("Trendbar sin timestamp conocido (ctm/utcTimestampInMinutes)")


def trendbars_to_df(trendbars, scale: float = 1e5) -> pd.DataFrame:
    """
    Convierte ProtoOATrendbar[] en DataFrame OHLC indexado por datetime (UTC).
    """
    rows = []
    for tb in trendbars:
        ts_ms = get_tb_timestamp_ms(tb)
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        low_raw = tb.low
        open_raw = tb.low + tb.deltaOpen
        high_raw = tb.low + tb.deltaHigh
        close_raw = tb.low + tb.deltaClose

        rows.append(
            {
                "time": ts,
                "open": open_raw / scale,
                "high": high_raw / scale,
                "low": low_raw / scale,
                "close": close_raw / scale,
            }
        )

    return pd.DataFrame(rows).set_index("time").sort_index()


# ---------------------------------------------------------
# GLOBALS for flow
# ---------------------------------------------------------
_symbol_id: int | None = None
_h4_df: pd.DataFrame | None = None
_d1_df: pd.DataFrame | None = None
_m5_df: pd.DataFrame | None = None


# ---------------------------------------------------------
# REQUEST HELPERS
# ---------------------------------------------------------
def request_trendbars(client: Client, symbol_id: int, tf: str, candles: int):
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - candles * TF_MS[tf]

    print(f"\n📡 Solicitando {candles} velas {tf} de {TARGET_SYMBOL_NAME}")

    req = OAMsg.ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.symbolId = symbol_id
    req.period = TF_CODE[tf]
    req.fromTimestamp = from_ms
    req.toTimestamp = now_ms

    client.send(req)


# ---------------------------------------------------------
# STEP: after we have H4 + D1 + M5
# ---------------------------------------------------------
def run_strategy_simulation():
    global _h4_df, _d1_df, _m5_df

    assert _h4_df is not None
    assert _d1_df is not None
    assert _m5_df is not None

    # -------------------------
    # 1) Swings H4
    # -------------------------
    doji_price = DOJI_POINTS * POINT_SIZE
    merge_price = MERGE_POINTS * POINT_SIZE

    highs, lows = find_last_5_highs_lows_two_candle(
        df_h4=_h4_df,
        doji_points=doji_price,
        k=5,
        merge_threshold_points=merge_price,
    )

    print("\n🟠 H4 HIGHS (last 5, merged)")
    for h in highs:
        print(f"  {h.ts.isoformat()}  level={h.level:.5f}")

    print("\n🔵 H4 LOWS (last 5, merged)")
    for l in lows:
        print(f"  {l.ts.isoformat()}  level={l.level:.5f}")

    # -------------------------
    # 2) Bias
    # -------------------------
    eps_price = BIAS_EPS_POINTS * POINT_SIZE
    bias_res = determine_trend_bias_from_swings_flexible(
        highs=highs,
        lows=lows,
        eps=eps_price,
        min_points=MIN_POINTS_FOR_BIAS,
    )

    print("\n📈 TREND BIAS (H4 swings)")
    print(f"  bias={bias_res.bias}")
    print(f"  reason={bias_res.reason}")

    # -------------------------
    # 3) D1 close (último D1 cerrado)
    # -------------------------
    # Tomamos el último close disponible (en general es el último D1 completo que te devuelve el server).
    d1_last_close = float(_d1_df["close"].iloc[-1])
    d1_day_id = _d1_df.index[-1].date().isoformat()

    # Niveles del día: por ahora usamos TODOS los swings (highs+lows) pero filtrados por close D1 según bias
    all_levels = [x.level for x in highs] + [x.level for x in lows]

    if bias_res.bias == "bullish":
        active_levels = sorted([lvl for lvl in all_levels if lvl < d1_last_close])
    elif bias_res.bias == "bearish":
        active_levels = sorted([lvl for lvl in all_levels if lvl > d1_last_close])
    else:
        active_levels = []

    print("\n📌 DAILY LEVELS (filtered by D1 close + bias)")
    print(f"  D1 last close={d1_last_close:.5f}  day_id={d1_day_id}")
    print(f"  total swings={len(all_levels)}  active_levels={len(active_levels)}")
    for lvl in active_levels:
        print(f"   - {lvl:.5f}")

    # -------------------------
    # 4) Crear plan diario
    # -------------------------
    plan = DailyPlan(
        day_id=d1_day_id,
        bias=bias_res.bias,
        levels=active_levels,
        zone_price=ZONE_POINTS * POINT_SIZE,
        close_eps=CLOSE_EPS_POINTS * POINT_SIZE,
        break_eps=BREAK_EPS_POINTS * POINT_SIZE,
        max_wait_bars=MAX_WAIT_BARS,
        sma_period=9,
    )

    # -------------------------
    # 5) Simulación M5: compute SMA9 y state machine
    # -------------------------
    df = _m5_df.copy()
    df["sma9"] = df["close"].rolling(plan.sma_period).mean()

    st = IntradayState()

    print("\n==============================")
    print("▶ SIMULACIÓN M5 (state machine)")
    print("==============================")
    print(f"  bias={plan.bias}  levels={len(plan.levels)}  zone={plan.zone_price:.8f}")
    print(f"  close_eps={plan.close_eps:.8f}  max_wait_bars={plan.max_wait_bars}")
    print("")

    last_printed_state = (st.state, st.candidate_level)

    for ts, r in df.iterrows():
        if pd.isna(r["sma9"]):
            continue

        ts_iso = ts.isoformat()

        prev_state = st.state
        prev_cand = st.candidate_level

        st, action = update_state_m5(
            plan,
            st,
            ts_iso=ts_iso,
            candle_open=float(r["open"]),
            candle_high=float(r["high"]),
            candle_low=float(r["low"]),
            candle_close=float(r["close"]),
            sma9=float(r["sma9"]),
        )

        changed = (st.state != prev_state) or (st.candidate_level != prev_cand)

        if changed:
            print(
                f"{ts_iso} | "
                f"state: {prev_state} -> {st.state} | "
                f"cand: {None if prev_cand is None else f'{prev_cand:.5f}'} -> "
                f"{None if st.candidate_level is None else f'{st.candidate_level:.5f}'} | "
                f"close={float(r['close']):.5f} sma9={float(r['sma9']):.5f} | "
                f"event={st.last_event}"
            )

        if action is not None:
            print("\n🚀 ACTION EMITIDA")
            print(action)
            break

    print("\n🏁 Fin simulación M5")


# ---------------------------------------------------------
# RESPONSE HANDLER (multi-step)
# ---------------------------------------------------------
def on_trendbars_res(message, client: Client):
    global _h4_df, _d1_df, _m5_df

    res = Protobuf.extract(message)  # ProtoOAGetTrendbarsRes
    trendbars = list(res.trendbar)

    if not trendbars:
        print("❌ No se recibieron velas")
        reactor.stop()
        return

    trendbars.sort(key=get_tb_timestamp_ms)

    # Detectar qué timeframe fue (no viene explícito en la res).
    # Lo resolvemos por "fase": pedimos H4, luego D1, luego M5.
    if _h4_df is None:
        _h4_df = trendbars_to_df(trendbars[-H4_CANDLES:])
        print(
            f"\n✅ H4 DF listo: candles={len(_h4_df)} "
            f"from={_h4_df.index.min().isoformat()} to={_h4_df.index.max().isoformat()}"
        )
        request_trendbars(client, _symbol_id, "D1", D1_CANDLES)
        return

    if _d1_df is None:
        _d1_df = trendbars_to_df(trendbars[-D1_CANDLES:])
        print(
            f"\n✅ D1 DF listo: candles={len(_d1_df)} "
            f"from={_d1_df.index.min().isoformat()} to={_d1_df.index.max().isoformat()}"
        )
        request_trendbars(client, _symbol_id, "M5", M5_CANDLES)
        return

    if _m5_df is None:
        _m5_df = trendbars_to_df(trendbars[-M5_CANDLES:])
        print(
            f"\n✅ M5 DF listo: candles={len(_m5_df)} "
            f"from={_m5_df.index.min().isoformat()} to={_m5_df.index.max().isoformat()}"
        )

        # correr simulación
        run_strategy_simulation()
        reactor.stop()
        return


# ---------------------------------------------------------
# SYMBOL LIST FLOW
# ---------------------------------------------------------
def on_symbols_list_res(message, client: Client):
    global _symbol_id
    res = Protobuf.extract(message)

    for s in res.symbol:
        if s.symbolName == TARGET_SYMBOL_NAME:
            _symbol_id = s.symbolId
            print(f"✅ Símbolo encontrado: {TARGET_SYMBOL_NAME} (id={_symbol_id})")
            request_trendbars(client, _symbol_id, "H4", H4_CANDLES)
            return

    print(f"❌ Símbolo no encontrado: {TARGET_SYMBOL_NAME}")
    reactor.stop()


# ---------------------------------------------------------
# AUTH FLOWS
# ---------------------------------------------------------
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
        on_trendbars_res(message, client)

    elif isinstance(decoded, OAMsg.ProtoOAErrorRes):
        print("❌ Error ProtoOA:", decoded)
        reactor.stop()


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    print(
        f"▶ Test M5 state machine\n"
        f"  symbol={TARGET_SYMBOL_NAME}\n"
        f"  H4_candles={H4_CANDLES}\n"
        f"  D1_candles={D1_CANDLES}\n"
        f"  M5_candles={M5_CANDLES}\n"
        f"  doji={DOJI_POINTS} points\n"
        f"  merge={MERGE_POINTS} points\n"
        f"  bias_eps={BIAS_EPS_POINTS} points\n"
        f"  zone={ZONE_POINTS} points\n"
        f"  close_eps={CLOSE_EPS_POINTS} points\n"
        f"  max_wait={MAX_WAIT_BARS} bars\n"
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
