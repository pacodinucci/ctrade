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


def _parse_date_param(value: Optional[str], name: str) -> Optional[date]:
    """
    Parsea un parámetro de fecha en formato YYYY-MM-DD.
    Si es None, devuelve None.
    Si el formato es inválido, lanza HTTP 400.
    """
    if value is None:
        return None

    value = value.strip()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Formato de '{name}' inválido: {value!r}. Usa YYYY-MM-DD.",
        )


def _load_parquet_timeframe(instrument: str, timeframe: str) -> pd.DataFrame:
    """
    Carga el parquet correspondiente al instrumento y timeframe
    y devuelve un DF indexado por tiempo (UTC).
    Ajustá el patrón de nombre si cambia.
    """
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
    start: Optional[str] = Query(
        None, description="Fecha inicio (YYYY-MM-DD, opcional)"
    ),
    end: Optional[str] = Query(
        None, description="Fecha fin (YYYY-MM-DD, opcional)"
    ),
    limit: int = Query(500, ge=1, le=5000),
):
    """
    Devuelve velas OHLC para un instrumento y timeframe.

    - `start` / `end` en formato YYYY-MM-DD (opcionales).
    - `limit` controla cuántas velas máximo devuelve (desde el final).
    """
    # 1) Parsear fechas a mano (evitamos errores de validación de FastAPI/Pydantic)
    start_date = _parse_date_param(start, "start")
    end_date = _parse_date_param(end, "end")

    # 2) Cargar histórico
    df = _load_parquet_timeframe(instrument, timeframe)

    # 3) Convertir a datetimes con tz UTC y filtrar
    if start_date is not None:
        start_dt = datetime.combine(start_date, time.min).replace(tzinfo=timezone.utc)
        df = df[df.index >= start_dt]

    if end_date is not None:
        end_dt = datetime.combine(end_date, time.max).replace(tzinfo=timezone.utc)
        df = df[df.index <= end_dt]

    if df.empty:
        raise HTTPException(status_code=404, detail="No hay datos en ese rango")

    # 4) Últimas `limit` velas
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
