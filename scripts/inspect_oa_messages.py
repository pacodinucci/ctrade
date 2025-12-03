# scripts/inspect_oa_messages.py
import sys
from pathlib import Path

# Añadir raíz del proyecto al sys.path (igual que en los otros scripts)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ctrader_open_api.messages import OpenApiMessages_pb2 as OAMsg

print("=== Mensajes que contienen 'Symbol' ===")
for name in dir(OAMsg):
    if "Symbol" in name or "symbol" in name:
        print(name)

print("\n=== Mensajes que contienen 'Spot' ===")
for name in dir(OAMsg):
    if "Spot" in name or "spot" in name:
        print(name)
