# scripts/test_double_trend.py

import sys
from pathlib import Path

# --- agregar raíz del proyecto al sys.path ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.two_trend_validation import validate_triple_trend


if __name__ == "__main__":
    # Uso:
    #   uv run scripts/test_double_trend.py GBPUSD
    #   uv run scripts/test_double_trend.py GBPUSD H4 H1 M30

    instrument = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    slow_tf = sys.argv[2] if len(sys.argv) > 2 else "H4"
    mid_tf = sys.argv[3] if len(sys.argv) > 3 else "H1"
    fast_tf = sys.argv[4] if len(sys.argv) > 4 else "M30"

    result = validate_triple_trend(
        instrument,
        slow_tf=slow_tf,
        mid_tf=mid_tf,
        fast_tf=fast_tf,
    )

    print(f"\n=== Validación triple de tendencia ({instrument}) ===")
    print(
        f"{slow_tf}: trend={result.slow.trend} "
        f"(HA última={result.slow.last_color})"
    )
    print(
        f"{mid_tf}:  trend={result.mid.trend} "
        f"(HA última={result.mid.last_color})"
    )
    print(
        f"{fast_tf}: trend={result.fast.trend} "
        f"(HA última={result.fast.last_color})"
    )

    print(f"\nAlineados: {result.aligned}")
    if result.bias == "long":
        print("Bias: LONG ✅  → Buscar disparador de COMPRA en M15")
    elif result.bias == "short":
        print("Bias: SHORT ✅ → Buscar disparador de VENTA en M15")
    else:
        print("Bias: NONE ⛔  → No buscar entrada, condición de tendencia NO cumplida")
