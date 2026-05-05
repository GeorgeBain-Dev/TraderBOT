from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

try:
    from .data import MT5Client, MT5Error
except ImportError:
    from data import MT5Client, MT5Error
try:
    from .utils import setup_logging
except ImportError:
    from utils import setup_logging

logger = setup_logging()


@dataclass
class TradePlan:
    direction: str  # "BUY" or "SELL"
    entry: float
    sl: float
    tp: float
    volume: float


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(value / step) * step


@dataclass
class RiskManager:
    risk_per_trade: float = 0.01
    sl_atr_mult: float = 1.5
    tp_rr: float = 2.0

    def calc_sl_tp(
            self,
            direction: str,
            entry: float,
            atr_value: float,
    ) -> Tuple[float, float]:
        sl_dist = max(float(atr_value) * self.sl_atr_mult, 1e-6)
        if direction.upper() == "BUY":
            sl = entry - sl_dist
            tp = entry + sl_dist * self.tp_rr
        else:
            sl = entry + sl_dist
            tp = entry - sl_dist * self.tp_rr
        return float(sl), float(tp)

    def calc_volume(
            self,
            client: Optional[MT5Client],
            symbol: str,
            entry: float,
            sl: float,
            balance: float,
    ) -> float:
        risk_amount = float(balance) * float(self.risk_per_trade)
        sl_distance = abs(float(entry) - float(sl))
        if sl_distance <= 0:
            return 0.0

        # Default fallback (backtest / MT5 not available): assume $10 per pip per 1.0 lot
        value_per_price_unit = None
        vol_min, vol_step, vol_max = 0.01, 0.01, 100.0

        if client is not None:
            try:
                info = client.symbol_info(symbol)
                tick_value = float(getattr(info, "trade_tick_value", 0.0))
                tick_size = float(getattr(info, "trade_tick_size", 0.0))
                vol_min = float(getattr(info, "volume_min", vol_min))
                vol_step = float(getattr(info, "volume_step", vol_step))
                vol_max = float(getattr(info, "volume_max", vol_max))
                if tick_value > 0 and tick_size > 0:
                    value_per_price_unit = tick_value / tick_size
            except MT5Error as e:
                logger.warning("Symbol info unavailable, using fallback sizing: %s", e)

        if value_per_price_unit is None:
            # crude fallback: treat 0.0001 move as $10 per lot for majors
            pip = 0.0001
            value_per_price_unit = 10.0 / pip

        raw_vol = risk_amount / (sl_distance * value_per_price_unit)
        vol = float(_round_step(raw_vol, vol_step))
        vol = max(vol_min, min(vol, vol_max))
        if not np.isfinite(vol) or vol <= 0:
            return 0.0
        return vol

    def plan_trade(
            self,
            direction: str,
            entry: float,
            atr_value: float,
            balance: float,
            client: Optional[MT5Client],
            symbol: str,
    ) -> TradePlan:
        sl, tp = self.calc_sl_tp(direction, entry, atr_value)
        volume = self.calc_volume(client=client, symbol=symbol, entry=entry, sl=sl, balance=balance)
        return TradePlan(direction=direction.upper(), entry=float(entry), sl=sl, tp=tp, volume=volume)

