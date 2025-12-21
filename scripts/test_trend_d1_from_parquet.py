# scripts/test_trend_d1_from_parquet.py

import sys
from pathlib import Path

import pandas as pd

# Para que pueda importar app.*
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.trading.trend_logic import add_indicators, get_trend, TrendType


DATA_DIR = ROOT / "data"


def load_d1_parquet(path: Path) -> pd.DataFrame:
    """
    Lee un PARQUET con columnas: open, high, low, close, volume
    y devuelve un DataFrame con índice datetime.
    En fetch_history guardamos el índice como 'time', así que acá
    lo recuperamos como índice temporal.
    """
    df = pd.read_parquet(path)

    # Si viene con la columna 'time', la usamos como índice.
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
    else:
        # Si el índice ya es el tiempo, lo convertimos a datetime por las dudas
        df.index = pd.to_datetime(df.index)

    df = df.sort_index()

    # Nos quedamos con las columnas clave
    return df[["open", "high", "low", "close", "volume"]]


def main():
    # 1) Ruta del PARQUET por CLI o default
    if len(sys.argv) > 1:
        parquet_path = Path(sys.argv[1])
        if not parquet_path.is_absolute():
            parquet_path = DATA_DIR / parquet_path
    else:
        # 👇 Ajustá este nombre si tu archivo se llama distinto
        parquet_path = DATA_DIR / "EURUSD_D1_2025-11-01_2025-11-30.parquet"

    if not parquet_path.exists():
        print(f"❌ No encontré el archivo: {parquet_path}")
        return

    print(f"📂 Leyendo histórico D1 desde: {parquet_path}")

    df = load_d1_parquet(parquet_path)

    # 2) Agregar EMA50 + Heiken Ashi
    df_ind = add_indicators(df)

    # 3) Calcular tendencia usando TODAS las velas del archivo
    trend: TrendType = get_trend(df_ind)
    last = df_ind.iloc[-1]

    print("\n=== RESULTADO trend_logic (D1) ===")
    print(f"Tendencia: {trend}")
    print(f"Último time : {df_ind.index[-1]}")
    print(f"Último close: {last['close']:.5f}")
    print(f"EMA50      : {last['EMA_50']:.5f}")
    print(f"HA_open    : {last['HA_open']:.5f}")
    print(f"HA_close   : {last['HA_close']:.5f}")

    print("\nÚltimas 5 velas con indicadores:")
    print(df_ind.tail())


if __name__ == "__main__":
    main()
