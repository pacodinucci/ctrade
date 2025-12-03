# scripts/test_ctrader_app_auth.py

import sys
from pathlib import Path

# 👇 Añadir raíz del proyecto al sys.path para que "app" se pueda importar
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg
from twisted.internet import reactor

from app.config import get_settings

settings = get_settings()


def on_error(failure):
    print("❌ Error en mensaje:", failure)
    reactor.stop()


def on_accounts_res(message):
    res = Protobuf.extract(message)
    print("✅ Accounts para este accessToken:\n")

    # Imprimimos el objeto crudo para ver los campos reales
    for acc in res.ctidTraderAccount:
        print(acc)

    print(
        "\n👉 Buscá en el output el campo `ctidTraderAccountId` "
        "de la cuenta demo que quieras usar y ponelo en CTRADER_ACCOUNT_ID en tu .env"
    )
    reactor.stop()


def on_app_auth_res(message, client: Client):
    res = Protobuf.extract(message)
    print("✅ Application AUTH OK\n")

    # Ahora pedimos la lista de cuentas ligadas al accessToken
    req = OAMsg.ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken = settings.CTRADER_ACCESS_TOKEN

    d = client.send(req)
    d.addErrback(on_error)


def on_connected(client: Client):
    print("🔌 Conectado al proxy DEMO")

    # 1) Autenticamos la APLICACIÓN
    req = OAMsg.ProtoOAApplicationAuthReq()
    req.clientId = settings.CTRADER_CLIENT_ID
    req.clientSecret = settings.CTRADER_CLIENT_SECRET

    d = client.send(req)
    d.addErrback(on_error)


def on_disconnected(client, reason):
    print("🔌 Desconectado:", reason)


def on_message_received(client: Client, message):
    decoded = Protobuf.extract(message)
    print("📩 Mensaje recibido tipo:", type(decoded).__name__)

    # Application auth OK → ahora pedimos cuentas
    if isinstance(decoded, OAMsg.ProtoOAApplicationAuthRes):
        on_app_auth_res(message, client)

    # Respuesta con lista de cuentas
    if isinstance(decoded, OAMsg.ProtoOAGetAccountListByAccessTokenRes):
        on_accounts_res(message)


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
