# app/trading/m5_state_machine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence, Dict, Any


TrendBias = Literal["bullish", "bearish", "neutral", "insufficient_data"]
State = Literal["off_zone", "in_zone", "rejection_wait_sma", "trigger", "trade"]


@dataclass(frozen=True)
class DailyPlan:
    day_id: str  # ej: "2025-12-21" (UTC)
    bias: TrendBias
    levels: list[float]  # niveles activos para el día (ya filtrados)
    zone_price: float
    close_eps: float
    break_eps: float
    max_wait_bars: int
    sma_period: int = 9


@dataclass
class IntradayState:
    state: State = "off_zone"
    candidate_level: Optional[float] = None
    bars_since_touch: int = 0
    # útil para debugging / trazabilidad
    last_event: str = ""
    # cuando ya abriste trade
    position_id: Optional[int] = None


def _select_candidate(bias: TrendBias, touched_levels: Sequence[float]) -> float:
    """
    En bearish (precio sube a niveles por arriba): candidato = el más alto tocado.
    En bullish (precio baja a niveles por abajo): candidato = el más bajo tocado.
    """
    if bias == "bearish":
        return max(touched_levels)
    if bias == "bullish":
        return min(touched_levels)
    # neutral/insufficient: no debería llamarse
    return touched_levels[-1]


def _touched_levels(
    bias: TrendBias,
    levels: Sequence[float],
    candle_high: float,
    candle_low: float,
    zone_price: float,
) -> list[float]:
    if bias == "bearish":
        # toca un nivel superior si el high llega cerca/por encima
        return [lvl for lvl in levels if candle_high >= (lvl - zone_price)]
    if bias == "bullish":
        # toca un nivel inferior si el low llega cerca/por debajo
        return [lvl for lvl in levels if candle_low <= (lvl + zone_price)]
    return []


def update_state_m5(
    plan: DailyPlan,
    st: IntradayState,
    *,
    ts_iso: str,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    sma9: float,
) -> tuple[IntradayState, Optional[Dict[str, Any]]]:
    """
    Máquina de estados:
      off_zone -> in_zone (touch)
      in_zone -> rejection_wait_sma (rejection del candidato)
      rejection_wait_sma -> trigger (confirmación SMA9)
      trigger -> trade (acción: open_trade)

    Además:
      - mientras in_zone, si toca otro nivel "más extremo" se actualiza candidate_level
      - mientras rejection_wait_sma, si vuelve a tocar niveles más extremos, se vuelve a in_zone con candidato nuevo
      - invalidación por timeout (max_wait_bars) en in_zone / rejection_wait_sma
    """
    action: Optional[Dict[str, Any]] = None

    # Si no hay sesgo utilizable o no hay niveles, no hay estrategia intradía
    if plan.bias not in ("bullish", "bearish") or not plan.levels:
        st.state = "off_zone"
        st.candidate_level = None
        st.bars_since_touch = 0
        st.last_event = f"{ts_iso} | no_plan (bias={plan.bias}, levels={len(plan.levels)})"
        return st, None

    # Si ya estamos en trade, no hacemos nada aquí (la gestión la hace otro módulo)
    if st.state == "trade":
        return st, None

    touched = _touched_levels(plan.bias, plan.levels, candle_high, candle_low, plan.zone_price)

    # ---- invalidación por tiempo ----
    if st.state in ("in_zone", "rejection_wait_sma"):
        st.bars_since_touch += 1
        if st.bars_since_touch >= plan.max_wait_bars:
            st.state = "off_zone"
            st.candidate_level = None
            st.bars_since_touch = 0
            st.last_event = f"{ts_iso} | invalidate_timeout"
            return st, None

    # ---- OFF_ZONE -> IN_ZONE (primer touch) ----
    if st.state == "off_zone":
        if touched:
            cand = _select_candidate(plan.bias, touched)
            st.state = "in_zone"
            st.candidate_level = cand
            st.bars_since_touch = 0
            st.last_event = f"{ts_iso} | off_zone->in_zone | cand={cand:.8f}"
        return st, None

    # ---- IN_ZONE: actualizar candidato y/o pasar a rejection_wait_sma ----
    if st.state == "in_zone":
        if touched:
            new_cand = _select_candidate(plan.bias, touched)
            if st.candidate_level is None:
                st.candidate_level = new_cand
                st.last_event = f"{ts_iso} | set_candidate | cand={new_cand:.8f}"
            else:
                # actualizar candidato solo si es "más extremo" que el actual
                if plan.bias == "bearish" and new_cand > st.candidate_level + plan.close_eps:
                    st.candidate_level = new_cand
                    st.bars_since_touch = 0
                    st.last_event = f"{ts_iso} | update_candidate_up | cand={new_cand:.8f}"
                elif plan.bias == "bullish" and new_cand < st.candidate_level - plan.close_eps:
                    st.candidate_level = new_cand
                    st.bars_since_touch = 0
                    st.last_event = f"{ts_iso} | update_candidate_down | cand={new_cand:.8f}"

        # rejection: cierre cruza el candidato en sentido de la tendencia
        if st.candidate_level is not None:
            if plan.bias == "bearish":
                # venía subiendo, candidato arriba, rechazo cuando cierra por debajo del candidato
                if candle_close < (st.candidate_level - plan.close_eps):
                    st.state = "rejection_wait_sma"
                    st.last_event = (
                        f"{ts_iso} | in_zone->rejection_wait_sma | "
                        f"close<{st.candidate_level:.8f}"
                    )
            else:  # bullish
                # venía bajando, candidato abajo, rechazo cuando cierra por arriba del candidato
                if candle_close > (st.candidate_level + plan.close_eps):
                    st.state = "rejection_wait_sma"
                    st.last_event = (
                        f"{ts_iso} | in_zone->rejection_wait_sma | "
                        f"close>{st.candidate_level:.8f}"
                    )

        return st, None

    # ---- REJECTION_WAIT_SMA: si toca niveles más extremos, volver a IN_ZONE; si confirma SMA, TRIGGER ----
    if st.state == "rejection_wait_sma":
        if touched:
            new_cand = _select_candidate(plan.bias, touched)
            if st.candidate_level is None:
                st.candidate_level = new_cand
                st.state = "in_zone"
                st.bars_since_touch = 0
                st.last_event = f"{ts_iso} | rejection->in_zone(set_candidate) | cand={new_cand:.8f}"
            else:
                # Si aparece un candidato "más extremo", el anterior deja de importar
                if plan.bias == "bearish" and new_cand > st.candidate_level + plan.close_eps:
                    st.candidate_level = new_cand
                    st.state = "in_zone"
                    st.bars_since_touch = 0
                    st.last_event = f"{ts_iso} | rejection->in_zone(update_candidate_up) | cand={new_cand:.8f}"
                    return st, None
                elif plan.bias == "bullish" and new_cand < st.candidate_level - plan.close_eps:
                    st.candidate_level = new_cand
                    st.state = "in_zone"
                    st.bars_since_touch = 0
                    st.last_event = f"{ts_iso} | rejection->in_zone(update_candidate_down) | cand={new_cand:.8f}"
                    return st, None

        # Confirmación SMA9
        if plan.bias == "bearish":
            if candle_close < sma9:
                st.state = "trigger"
                st.last_event = f"{ts_iso} | rejection_wait_sma->trigger | close<sma9"
        else:  # bullish
            if candle_close > sma9:
                st.state = "trigger"
                st.last_event = f"{ts_iso} | rejection_wait_sma->trigger | close>sma9"

        return st, None

    # ---- TRIGGER -> TRADE (emitir acción) ----
    if st.state == "trigger":
        if plan.bias == "bearish":
            side = "sell"
        else:
            side = "buy"

        action = {
            "type": "open_trade",
            "side": side,
            "candidate_level": st.candidate_level,
            "ts": ts_iso,
            "note": "trigger confirmed by SMA9",
        }
        st.state = "trade"
        st.last_event = f"{ts_iso} | trigger->trade | action=open_trade({side})"
        return st, action

    return st, None
