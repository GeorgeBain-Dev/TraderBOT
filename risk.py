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


def _require_mt5_module():
    try:
        import MetaTrader5 as mt5  # type: ignore

        return mt5
    except Exception as e:
        raise MT5Error(f"MetaTrader5 not available: {e}") from e


def loss_at_stop(
    client: Optional[MT5Client],
    symbol: str,
    direction: str,
    volume: float,
    entry: float,
    sl: float,
) -> float:
    """Loss in account currency if SL is hit (uses MT5 symbol contract math)."""
    if client is None or volume <= 0:
        return 0.0
    try:
        if not client.initialize():
            return 0.0
        mt5 = _require_mt5_module()
        order_type = (
            mt5.ORDER_TYPE_BUY if direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        )
        profit = mt5.order_calc_profit(order_type, symbol, float(volume), float(entry), float(sl))
        if profit is None:
            logger.warning("order_calc_profit failed for %s: %s", symbol, mt5.last_error())
            return 0.0
        return abs(float(profit))
    except Exception as e:
        logger.warning("loss_at_stop failed for %s: %s", symbol, e)
        return 0.0


def profit_at_price(
    client: Optional[MT5Client],
    symbol: str,
    direction: str,
    volume: float,
    entry: float,
    price: float,
) -> float:
    """Expected profit in account currency at a specified price level."""
    if client is None or volume <= 0:
        return 0.0
    try:
        if not client.initialize():
            return 0.0
        mt5 = _require_mt5_module()
        order_type = (
            mt5.ORDER_TYPE_BUY if direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        )
        profit = mt5.order_calc_profit(order_type, symbol, float(volume), float(entry), float(price))
        if profit is None:
            logger.warning("order_calc_profit failed for %s: %s", symbol, mt5.last_error())
            return 0.0
        return abs(float(profit))
    except Exception as e:
        logger.warning("profit_at_price failed for %s: %s", symbol, e)
        return 0.0


def value_per_price_unit(client: Optional[MT5Client], symbol: str) -> float:
    """Account-currency value of a 1.0 price-unit move per 1.0 lot."""
    if client is not None:
        try:
            info = client.symbol_info(symbol)
            tick_value = float(getattr(info, "trade_tick_value", 0.0))
            tick_size = float(getattr(info, "trade_tick_size", 0.0))
            if tick_value > 0 and tick_size > 0:
                return tick_value / tick_size
        except MT5Error as e:
            logger.warning("Symbol info unavailable for risk money: %s", e)
    return 10.0 / 0.0001


def money_at_risk(
    client: Optional[MT5Client],
    symbol: str,
    entry: float,
    sl: float,
    volume: float,
    direction: str = "BUY",
) -> float:
    """Estimated loss in account currency if price hits stop loss."""
    if volume <= 0:
        return 0.0
    mt5_loss = loss_at_stop(client, symbol, direction, volume, entry, sl)
    if mt5_loss > 0:
        return mt5_loss
    sl_distance = abs(float(entry) - float(sl))
    if sl_distance <= 0:
        return 0.0
    vppu = value_per_price_unit(client, symbol)
    return max(sl_distance * float(volume) * vppu, 0.0)


@dataclass
class RiskManager:
    risk_per_trade: float = 0.01
    sl_atr_mult: float = 1.5  # Tighter stop loss
    tp_rr: float = 2.5  # Higher risk/reward for profitability

    def calc_sl_tp(
            self,
            direction: str,
            entry: float,
            atr_value: float,
    ) -> Tuple[float, float]:
        sl_dist = max(float(atr_value) * self.sl_atr_mult, 2e-6)
        if direction.upper() == "BUY":
            sl = entry - sl_dist
            tp = entry + sl_dist * self.tp_rr
        else:
            sl = entry + sl_dist
            tp = entry - sl_dist * self.tp_rr
        return float(sl), float(tp)

    def plan_trade(
            self,
            direction: str,
            entry: float,
            atr_value: float,
            balance: float,
            client: Optional[MT5Client],
            symbol: str,
            confidence: float = 0.5,  # Signal confidence for risk-based sizing
    ) -> TradePlan:
        direction_u = direction.upper()
        sl, tp = self.calc_sl_tp(direction_u, entry, atr_value)
        volume = self._calc_volume_for_direction(
            client=client,
            symbol=symbol,
            direction=direction_u,
            entry=entry,
            sl=sl,
            balance=balance,
            confidence=confidence,  # Pass confidence for dynamic sizing
        )
        return TradePlan(
            direction=direction_u,
            entry=float(entry),
            sl=sl,
            tp=tp,
            volume=volume,
        )

    def _calc_volume_for_direction(
        self,
        client: Optional[MT5Client],
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        balance: float,
        confidence: float = 0.5,  # Signal confidence for risk-based sizing
    ) -> float:
        # Adjust risk based on signal confidence
        # Higher confidence = larger position size (up to 1.5x base risk)
        # Lower confidence = smaller position size (down to 0.5x base risk)
        confidence_multiplier = 0.5 + confidence  # Range: 0.5 to 1.5
        adjusted_risk_per_trade = float(self.risk_per_trade) * confidence_multiplier
        
        risk_amount = float(balance) * adjusted_risk_per_trade
        sl_distance = abs(float(entry) - float(sl))
        if sl_distance <= 0:
            return 0.0

        vol_min, vol_step, vol_max = 0.01, 0.01, 100.0
        if client is not None:
            try:
                info = client.symbol_info(symbol)
                vol_min = float(getattr(info, "volume_min", vol_min))
                vol_step = float(getattr(info, "volume_step", vol_step))
                vol_max = float(getattr(info, "volume_max", vol_max))
            except MT5Error as e:
                logger.warning("Symbol info unavailable, using fallback sizing: %s", e)

        loss_per_lot = loss_at_stop(client, symbol, direction, 1.0, entry, sl)
        if loss_per_lot > 0:
            raw_vol = risk_amount / loss_per_lot
        else:
            vppu = value_per_price_unit(client, symbol)
            raw_vol = risk_amount / (sl_distance * vppu)

        vol = float(_round_step(raw_vol, vol_step))
        vol = max(vol_min, min(vol, vol_max))
        if not np.isfinite(vol) or vol <= 0:
            return 0.0

        # Shrink volume if rounding pushed risk over budget
        while vol >= vol_min:
            actual = loss_at_stop(client, symbol, direction, vol, entry, sl)
            if actual <= 0 or actual <= risk_amount * 1.02:
                break
            vol = max(vol_min, vol - vol_step)

        return vol

