from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from .data import MT5Client, MT5Error, _require_mt5
except ImportError:
    from data import MT5Client, MT5Error, _require_mt5
try:
    from .utils import setup_logging
except ImportError:
    from utils import setup_logging

logger = setup_logging()


@dataclass
class ExecutionResult:
    ok: bool
    message: str
    order_id: Optional[int] = None
    retcode: Optional[int] = None


@dataclass
class ExecutionEngine:
    client: MT5Client
    deviation_points: int = 20
    magic_number: int = 0
    comment: str = "pybot"
    allow_live_trading: bool = True

    def positions(self, symbol: Optional[str] = None) -> List[Any]:
        mt5 = _require_mt5()
        if not self.client.initialize():
            raise MT5Error("MT5 not initialized")
        if symbol:
            pos = mt5.positions_get(symbol=symbol)
        else:
            pos = mt5.positions_get()
        return list(pos) if pos is not None else []

    def has_position(self, symbol: str) -> bool:
        return len(self.positions(symbol=symbol)) > 0

    def place_market_order(
        self,
        symbol: str,
        direction: str,
        volume: float,
        sl: float,
        tp: float,
    ) -> ExecutionResult:
        mt5 = _require_mt5()
        if not self.client.initialize():
            return ExecutionResult(False, "MT5 initialize failed")
        self.client.ensure_symbol(symbol)

        if not self.allow_live_trading:
            msg = f"Live trading disabled. Would place {direction} {symbol} vol={volume}"
            logger.info(msg)
            return ExecutionResult(True, msg, order_id=None)

        if volume <= 0:
            return ExecutionResult(False, "Volume <= 0; risk sizing produced invalid volume")

        tick = self.client.tick(symbol)
        bid = float(getattr(tick, "bid", 0.0))
        ask = float(getattr(tick, "ask", 0.0))

        direction_u = direction.upper()
        if direction_u == "BUY":
            order_type = mt5.ORDER_TYPE_BUY
            price = ask
        elif direction_u == "SELL":
            order_type = mt5.ORDER_TYPE_SELL
            price = bid
        else:
            return ExecutionResult(False, f"Unknown direction: {direction}")

        info = self.client.symbol_info(symbol)
        # Get the correct filling mode for the symbol
        filling = getattr(info, "filling_mode", None)
        if filling is None:
            # Default to FOK (Fill or Kill) for most symbols
            filling = mt5.ORDER_FILLING_FOK
        
        # Try different filling modes if the first one fails
        filling_modes = [filling, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]
        
        for mode in filling_modes:
            request: Dict[str, Any] = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": order_type,
                "price": float(price),
                "sl": float(sl),
                "tp": float(tp),
                "deviation": int(self.deviation_points),
                "magic": int(self.magic_number),
                "comment": self.comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mode,
            }

            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                ticket = self.find_position_ticket(symbol)
                sl_ok, sl_msg = self.verify_position_stops(
                    symbol, ticket, expected_sl=sl, expected_tp=tp
                )
                msg = (
                    f"Order placed: {direction} {symbol} vol={volume} price={price} "
                    f"sl={sl} tp={tp} | {sl_msg}"
                )
                if not sl_ok:
                    logger.warning(msg)
                return ExecutionResult(True, msg, order_id=result.order)
            elif result is not None:
                logger.warning(f"Filling mode {mode} failed with retcode={result.retcode}: {result.comment}")
                continue
        
        # If all filling modes failed, return the last error
        if result is None:
            return ExecutionResult(False, f"order_send returned None: {mt5.last_error()}")
        else:
            return ExecutionResult(False, f"Order failed retcode={result.retcode} comment={result.comment}")

    def verify_position_stops(
        self,
        symbol: str,
        ticket: Optional[int],
        *,
        expected_sl: float,
        expected_tp: float,
    ) -> tuple[bool, str]:
        """Confirm broker accepted SL/TP on the open position."""
        if not ticket:
            return False, "no position ticket to verify"
        try:
            import MetaTrader5 as mt5

            pos = mt5.positions_get(ticket=ticket)
            if not pos:
                return False, f"position {ticket} not found after order"
            p = pos[0]
            actual_sl = float(getattr(p, "sl", 0.0) or 0.0)
            actual_tp = float(getattr(p, "tp", 0.0) or 0.0)
            if actual_sl <= 0:
                return (
                    False,
                    f"WARNING: position {ticket} has NO stop loss on broker "
                    f"(requested sl={expected_sl:.5f})",
                )
            return (
                True,
                f"position {ticket} sl={actual_sl:.5f} tp={actual_tp:.5f}",
            )
        except Exception as e:
            return False, f"stop verification failed: {e}"

    def find_position_ticket(
        self,
        symbol: str,
        *,
        after_order: Optional[int] = None,
    ) -> Optional[int]:
        """Resolve position ticket by symbol/magic (optionally after a new order)."""
        positions = self.positions(symbol=symbol)
        if not positions:
            return None
        if after_order is not None:
            for p in positions:
                if int(getattr(p, "identifier", 0) or 0) == after_order:
                    return int(getattr(p, "ticket", 0) or 0)
        for p in positions:
            if int(getattr(p, "magic", 0) or 0) == int(self.magic_number):
                return int(getattr(p, "ticket", 0) or 0)
        return int(getattr(positions[0], "ticket", 0) or 0)

    def close_position(self, ticket: int, close_direction: str) -> ExecutionResult:
        """Close an existing position. close_direction is the closing deal side (SELL closes BUY)."""
        if not self.client.initialize():
            return ExecutionResult(False, "MT5 initialize failed")

        try:
            import MetaTrader5 as mt5

            position = mt5.positions_get(ticket=ticket)
            if not position:
                return ExecutionResult(False, f"Position {ticket} not found")

            pos = position[0]
            symbol = pos.symbol
            volume = pos.volume

            close_dir = close_direction.upper()
            if close_dir == "SELL":
                order_type = mt5.ORDER_TYPE_SELL
                price = float(self.client.tick(symbol).bid)
            else:
                order_type = mt5.ORDER_TYPE_BUY
                price = float(self.client.tick(symbol).ask)
            
            # Create close request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "position": ticket,
                "price": price,
                "deviation": int(self.deviation_points),
                "magic": int(self.magic_number),
                "comment": "Close position",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result is None:
                return ExecutionResult(False, f"Close order failed: {mt5.last_error()}")
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return ExecutionResult(False, f"Close order failed retcode={result.retcode}: {result.comment}")
            
            return ExecutionResult(True, f"Position {ticket} closed successfully", order_id=result.order)
            
        except Exception as e:
            return ExecutionResult(False, f"Error closing position: {e}")
    
    def normalize_stop_loss(
        self,
        symbol: str,
        position_side: str,
        reference_price: float,
        stop_loss: float,
    ) -> float:
        """Clamp SL to broker minimum stop distance from reference price."""
        try:
            info = self.client.symbol_info(symbol)
            point = float(getattr(info, "point", 0.0) or 0.00001)
            stops_level = int(getattr(info, "trade_stops_level", 0) or 0)
            min_dist = max(stops_level * point, point)
        except Exception:
            min_dist = 0.0001

        side = position_side.upper()
        if side == "BUY":
            max_sl = reference_price - min_dist
            return min(float(stop_loss), max_sl)
        max_sl = reference_price + min_dist
        return max(float(stop_loss), max_sl)

    def modify_stop_loss(self, ticket: int, new_stop_loss: float) -> ExecutionResult:
        """Modify stop loss for an existing position"""
        if not self.client.initialize():
            return ExecutionResult(False, "MT5 initialize failed")

        try:
            import MetaTrader5 as mt5

            position = mt5.positions_get(ticket=ticket)
            if not position:
                return ExecutionResult(False, f"Position {ticket} not found")

            pos = position[0]
            symbol = pos.symbol
            ptype = int(getattr(pos, "type", 0))
            side = "BUY" if ptype == 0 else "SELL"
            tick = self.client.tick(symbol)
            ref = float(tick.bid if side == "BUY" else tick.ask)
            sl = self.normalize_stop_loss(symbol, side, ref, new_stop_loss)

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "sl": sl,
                "tp": pos.tp,
                "position": ticket,
                "magic": int(self.magic_number),
                "comment": "Modify stop loss",
            }
            
            result = mt5.order_send(request)
            if result is None:
                return ExecutionResult(False, f"Modify SL failed: {mt5.last_error()}")
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return ExecutionResult(False, f"Modify SL failed retcode={result.retcode}: {result.comment}")
            
            return ExecutionResult(
                True,
                f"Stop loss modified to {sl} for position {ticket}",
                order_id=result.order,
            )
            
        except Exception as e:
            return ExecutionResult(False, f"Error modifying stop loss: {e}")

