# app/api/routes_history.py
from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import List, Optional, Dict

import pandas as pd
from fastapi import APIRouter, Query, HTTPException

router = APIRouter(prefix="/history", tags=["history"])

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


def _load_parquet_timeframe(instrument: str, timeframe: str) -> pd.DataFrame:
    """
    Carga el parquet correspondiente al instrumento y timeframe
    y devuelve un DF indexado por tiempo (UTC).
    """
    # Ajustá el patrón si cambian los nombres de archivo
    file_name = f"{instrument}_{timeframe}_2025-08-01_2025-11-30.parquet"
    path = DATA_DIR / file_name

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Histórico no encontrado: {path}",
        )

    df = pd.read_parquet(path)

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time")
    else:
        df.index = pd.to_datetime(df.index, utc=True)

    return df.sort_index()


@router.get("/{instrument}/{timeframe}", summary="Histórico OHLC para gráficos")
async def get_history(
    instrument: str,
    timeframe: str,
    start: Optional[date] = Query(
        None, description="Fecha inicio (YYYY-MM-DD, opcional)"
    ),
    end: Optional[date] = Query(
        None, description="Fecha fin (YYYY-MM-DD, opcional)"
    ),
    limit: int = Query(500, ge=1, le=5000),
):
    """
    Devuelve velas OHLC para un instrumento y timeframe.

    - Si se pasan `start` y/o `end`, se filtra por rango de fechas.
    - `limit` controla cuántas velas máximo devuelve (desde el final).
    """
    df = _load_parquet_timeframe(instrument, timeframe)

    # Convertimos start/end a datetimes con tz UTC
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None

    if start is not None:
        start_dt = datetime.combine(start, time.min).replace(tzinfo=timezone.utc)
    if end is not None:
        end_dt = datetime.combine(end, time.max).replace(tzinfo=timezone.utc)

    if start_dt is not None:
        df = df[df.index >= start_dt]
    if end_dt is not None:
        df = df[df.index <= end_dt]

    if df.empty:
        raise HTTPException(status_code=404, detail="No hay datos en ese rango")

    # Nos quedamos con las últimas `limit` velas
    df = df.tail(limit)

    candles: List[Dict] = []
    for idx, row in df.iterrows():
        candles.append(
            {
                "time": idx.isoformat(),   # ISO string
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )

    return {
        "instrument": instrument,
        "timeframe": timeframe,
        "count": len(candles),
        "candles": candles,
    }
