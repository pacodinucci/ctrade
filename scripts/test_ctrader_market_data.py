# scripts/test_ctrader_market_data.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg
from twisted.internet import reactor

from app.config import get_settings

settings = get_settings()

TARGET_SYMBOL_NAME = "EURUSD"

# 👇 IMPORTANTE: este es el ctidTraderAccountId (accountId en el JSON),
# NO el accountNumber.
CTID_TRADER_ACCOUNT_ID = 45440970  # <-- poné acá el "accountId" de tu JSON


def on_error(failure):
    print("❌ Error en mensaje (Deferred):", failure)
    reactor.stop()


# ---------------- SPOT EVENT -----------------

def on_spot_event(message):
    decoded = Protobuf.extract(message)  # ProtoOASpotEvent
    symbol_id = decoded.symbolId
    bid = decoded.bid
    ask = decoded.ask
    mid = (bid + ask) / 2

    print(f"📈 Spot (symbolId={symbol_id}) → bid={bid} ask={ask} mid={mid}")


# ---------------- SYMBOLS LIST ----------------

def on_symbols_list_res(message, client: Client):
    res = Protobuf.extract(message)  # ProtoOASymbolsListRes

    eurusd_symbol_id = None

    print("\n=== Algunos símbolos recibidos ===")
    for i, s in enumerate(res.symbol[:30]):
        name = getattr(s, "symbolName", None)
        sid = getattr(s, "symbolId", None)
        print(s)
        if name is not None and sid is not None:
            print(f"  -> id={sid} name={name}")
            if name == TARGET_SYMBOL_NAME:
                eurusd_symbol_id = sid

    if eurusd_symbol_id is None:
        print(f"\n⚠️ No encontré {TARGET_SYMBOL_NAME} en la lista de símbolos")
        reactor.stop()
        return

    print(f"\n✅ Encontrado {TARGET_SYMBOL_NAME} con symbolId={eurusd_symbol_id}")
    print("📡 Enviando ProtoOASubscribeSpotsReq...\n")

    req = OAMsg.ProtoOASubscribeSpotsReq()
    req.ctidTraderAccountId = CTID_TRADER_ACCOUNT_ID
    req.symbolId.append(eurusd_symbol_id)
    req.subscribeToSpotTimestamp = False

    d = client.send(req)
    d.addErrback(on_error)


# ---------------- AUTH FLOWS ----------------

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
    print("🔌 Conectado al proxy DEMO (market data)")

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

    elif isinstance(decoded, OAMsg.ProtoOASpotEvent):
        on_spot_event(message)

    elif isinstance(decoded, OAMsg.ProtoOAErrorRes):
        # 👇 logueamos bien el error
        print("❌ ProtoOAErrorRes recibido:")
        print(decoded)
        # si querés, podés cortar acá:
        reactor.stop()


def main():
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
