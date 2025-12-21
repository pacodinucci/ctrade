# scripts/backtest_trend_d1_with_levels.py

import sys
from pathlib import Path
import pandas as pd

# ---- PATHS ----
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.trend_logic import add_indicators, get_trend
from app.trading.levels import detect_levels, sort_levels_by_distance

DATA_DIR = ROOT / "data"


# =========================
# Loader parquet
# =========================
def load_parquet(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
    else:
        df.index = pd.to_datetime(df.index)

    return df.sort_index()


# =========================
# Main
# =========================
def main():
    # ---- archivos por defecto / CLI ----
    d1_path = DATA_DIR / "EURUSD_D1_2025-08-01_2025-11-30.parquet"
    h1_path = DATA_DIR / "EURUSD_H1_2025-08-01_2025-11-30.parquet"

    if len(sys.argv) > 1:
        d1_arg = Path(sys.argv[1])
        if not d1_arg.is_absolute():
            d1_arg = DATA_DIR / d1_arg
        d1_path = d1_arg

    if len(sys.argv) > 2:
        h1_arg = Path(sys.argv[2])
        if not h1_arg.is_absolute():
            h1_arg = DATA_DIR / h1_arg
        h1_path = h1_arg

    if not d1_path.exists():
        print(f"❌ No existe D1: {d1_path}")
        return
    if not h1_path.exists():
        print(f"❌ No existe H1: {h1_path}")
        return

    print(f"📂 Cargando D1 desde: {d1_path}")
    print(f"📂 Cargando H1 desde: {h1_path}")

    df_d1 = load_parquet(d1_path)
    df_h1 = load_parquet(h1_path)

    # Añadimos EMA50 + Heiken Ashi al D1
    df_d1_ind = add_indicators(df_d1)

    # ---- Rango de análisis ----
    start = "2025-11-01"
    end = "2025-11-30"

    # ---- Parámetros de niveles ----
    tol_pips = 10
    tol_price = tol_pips * 0.0001
    min_touches = 3

    print(
        "\n📊 TENDENCIA + NIVELES (por día)"
        "\nFecha        | Tendencia | Close D1 | Niveles relevantes"
    )
    print("-" * 90)

    # =========================
    # LOOP DIARIO
    # =========================
    for t, row in df_d1_ind.iterrows():
        date_str = t.strftime("%Y-%m-%d")

        if not (start <= date_str <= end):
            continue

        # Slice D1 hasta ese día
        df_d1_slice = df_d1_ind.loc[:t]

        trend = get_trend(df_d1_slice)
        last = df_d1_slice.iloc[-1]
        close_d1 = float(last["close"])

        # Si la tendencia no está definida → skip
        if trend not in ("bullish", "bearish"):
            print(f"{date_str} | {trend:9s} | {close_d1:.5f} | (sin niveles, tendencia indefinida)")
            continue

        # Slice de H1 hasta fin del día
        day_end = t.replace(hour=23, minute=59, second=59, microsecond=0)
        df_h1_slice = df_h1.loc[:day_end]

        if len(df_h1_slice) < 50:
            print(f"{date_str} | {trend:9s} | {close_d1:.5f} | (muy pocos datos H1)")
            continue

        # ---- Detectar niveles en H1 ----
        sup_levels, res_levels = detect_levels(
            df_h1_slice,
            tol_price=tol_price,
            min_touches=min_touches,
        )

        # ---- Selección según tendencia ----
        if trend == "bullish":
            chosen = sup_levels
            side_label = "SOPORTES"
        else:
            chosen = res_levels
            side_label = "RESISTENCIAS"

        if not chosen:
            print(f"{date_str} | {trend:9s} | {close_d1:.5f} | {side_label}: ninguno")
            continue

        # Ordenar por cercanía al precio D1
        chosen_sorted = sort_levels_by_distance(chosen, close_d1)
        top = chosen_sorted[:3]

        parts = []
        for lvl in top:
            price = lvl["price"]
            touches = lvl["touches"]
            dist_pips = abs(price - close_d1) / 0.0001
            parts.append(f"{price:.5f} ({touches}t, {dist_pips:.1f} pips)")

        levels_str = f"{side_label}: " + " | ".join(parts)

        print(f"{date_str} | {trend:9s} | {close_d1:.5f} | {levels_str}")


if __name__ == "__main__":
    main()
