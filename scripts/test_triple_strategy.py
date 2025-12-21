# scripts/test_triple_strategy.py

import sys
from pathlib import Path

# --- agregar raíz del proyecto al sys.path ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.triple_strategy import evaluate_triple_tf_strategy_with_trigger


if __name__ == "__main__":
    # Uso:
    #   uv run scripts/test_triple_strategy.py GBPUSD
    #   uv run scripts/test_triple_strategy.py EURUSD

    instrument = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"

    decision = evaluate_triple_tf_strategy_with_trigger(
        instrument,
        slow_tf="H4",
        mid_tf="H1",
        fast_tf="M30",
        trigger_tf="M15",
        sl_points=200,
        tp_points=100,
    )

    print(f"\n=== Estrategia triple TF + trigger M15 ({instrument}) ===")
    print(f"Bias (H4/H1/M30): {decision.bias}")

    if not decision.should_trade:
        print("→ Sin trade: o no hay alineación de tendencia o no hay trigger en M15.")
    else:
        print(f"\nSEÑAL: {decision.trigger_side.upper()} via {decision.trigger_type}")
        print(f"Entrada:   {decision.entry_price:.5f}")
        print(f"Stop Loss: {decision.stop_loss:.5f}  (200 puntos)")
        print(f"Take Prof: {decision.take_profit:.5f}  (100 puntos)")
