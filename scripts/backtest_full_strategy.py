# scripts/backtest_full_strategy.py

import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.trend_logic import add_indicators, get_trend
from app.trading.levels import detect_levels, sort_levels_by_distance
from app.trading.john_wicks_logic import classify_john_wicks, MIN_WICK_RATIO

DATA_DIR = ROOT / "data"


# ----------------------------------------------------------
# LOAD PARQUET
# ----------------------------------------------------------
def load_parquet(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
    else:
        df.index = pd.to_datetime(df.index)

    return df.sort_index()


# ----------------------------------------------------------
# CHECK SIGNAL M15
# ----------------------------------------------------------
def find_m15_johnwick_signal(df_m15_day, levels, max_dist_pts=10):
    """
    df_m15_day: M15 del día siguiente.
    levels: lista de niveles (ya ordenados).
    """

    if df_m15_day.empty:
        return None

    df_m15 = classify_john_wicks(df_m15_day.copy(), min_wick_ratio=MIN_WICK_RATIO)
    df_jw = df_m15[df_m15["john_wick_type"] != "none"]

    if df_jw.empty:
        return None

    results = []

    for idx, row in df_jw.iterrows():
        close = float(row["close"])

        for lvl in levels:
            lvl_price = lvl["price"]
            dist_pts = abs(close - lvl_price) / 0.0001

            if dist_pts <= max_dist_pts:
                results.append({
                    "time": idx,
                    "jw_type": row["john_wick_type"],
                    "price": close,
                    "level_price": lvl_price,
                    "dist_pts": dist_pts,
                })

    if not results:
        return None

    # devolver el JW más cercano
    return min(results, key=lambda r: r["dist_pts"])


# ----------------------------------------------------------
# MAIN
# ----------------------------------------------------------
def main():
    print("📂 Cargando históricos...")

    d1_path = DATA_DIR / "EURUSD_D1_2025-08-01_2025-11-30.parquet"
    h1_path = DATA_DIR / "EURUSD_H1_2025-08-01_2025-11-30.parquet"
    m15_path = DATA_DIR / "EURUSD_M15_2025-08-01_2025-11-30.parquet"

    df_d1 = load_parquet(d1_path)
    df_h1 = load_parquet(h1_path)
    df_m15 = load_parquet(m15_path)

    df_d1_ind = add_indicators(df_d1)

    start = "2025-11-01"
    end = "2025-11-30"

    tol_pips = 10
    tol_price = tol_pips * 0.0001
    min_touches = 3

    print("\n📊 BACKTEST COMPLETO")
    print("Fecha | Tendencia | Señal")

    # ------------------------------------------------------
    # LOOP POR CADA DÍA
    # ------------------------------------------------------
    for t, row in df_d1_ind.iterrows():
        date_str = t.strftime("%Y-%m-%d")
        if not (start <= date_str <= end):
            continue

        # Tendencia usando solo datos previos
        df_slice = df_d1_ind.loc[:t]
        trend = get_trend(df_slice)

        if trend not in ("bullish", "bearish"):
            print(f"{date_str} | {trend:8s} | ---")
            continue

        close_d1 = float(df_slice.iloc[-1]["close"])

        # Día siguiente
        next_day = (t + pd.Timedelta(days=1)).normalize()

        # --------------------------------------------------
        # Niveles de H1 usando últimas 200 velas antes de 00:00 del día siguiente
        # --------------------------------------------------
        h1_before_nextday = df_h1.loc[:next_day]
        df_h1_tail = h1_before_nextday.tail(200)

        if len(df_h1_tail) < 50:
            print(f"{date_str} | {trend:8s} | pocos datos H1 | ---")
            continue

        sup, res = detect_levels(df_h1_tail, tol_price=tol_price, min_touches=min_touches)

        if trend == "bullish":
            chosen = sup      # buscamos soportes
        else:
            chosen = res      # buscamos resistencias

        if not chosen:
            print(f"{date_str} | {trend:8s} | sin niveles | ---")
            continue

        # Seleccionamos los 5 niveles más cercanos
        chosen_sorted = sort_levels_by_distance(chosen, close_d1)[:5]

        # --------------------------------------------------
        # Buscar señal JW en M15 del día siguiente
        # --------------------------------------------------
        df_m15_day = df_m15.loc[next_day : next_day + pd.Timedelta(hours=23, minutes=59)]

        signal = find_m15_johnwick_signal(df_m15_day, chosen_sorted)

        if not signal:
            print(f"{date_str} | {trend:8s} | ---")
        else:
            print(
                f"{date_str} | {trend:8s} | "
                f"JW {signal['time']} lvl={signal['level_price']:.5f} "
                f"dist={signal['dist_pts']:.1f}pts"
            )


if __name__ == "__main__":
    main()
