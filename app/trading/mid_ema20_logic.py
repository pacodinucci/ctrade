from app.trading.trend_logic import get_candles
from app.trading.levels import detect_levels, sort_levels_by_distance

def get_h1_levels_for_instrument(
    instrument: str,
    candles: int = 500,
    tol_price: float = 0.0010,  # ejemplo, ~10 pips en EURUSD
    min_touches: int = 3,
):
    df = get_candles(instrument, "H1", candles)

    sup_levels, res_levels = detect_levels(
        df,
        tol_price=tol_price,
        min_touches=min_touches,
    )

    last_close = float(df["close"].iloc[-1])

    sup_sorted = sort_levels_by_distance(sup_levels, last_close)
    res_sorted = sort_levels_by_distance(res_levels, last_close)

    return {
        "df": df,
        "last_close": last_close,
        "supports": sup_sorted,
        "resistances": res_sorted,
    }
