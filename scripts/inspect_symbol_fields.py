# scripts/inspect_symbol_fields.py
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg


def describe(msg_cls):
    msg = msg_cls()
    print(f"\n=== {msg_cls.__name__} ===")
    for f in msg.DESCRIPTOR.fields:
        print(f"- {f.name} (number={f.number}, label={f.label}, type={f.type})")


def main():
    describe(OAMsg.ProtoOASymbolsListReq)
    describe(OAMsg.ProtoOASymbolsListRes)
    describe(OAMsg.ProtoOASubscribeSpotsReq)
    describe(OAMsg.ProtoOASpotEvent)


if __name__ == "__main__":
    main()
