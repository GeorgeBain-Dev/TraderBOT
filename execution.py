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
                return ExecutionResult(True, f"Order placed: {direction} {symbol} vol={volume} price={price} sl={sl} tp={tp}", order_id=result.order)
            elif result is not None:
                logger.warning(f"Filling mode {mode} failed with retcode={result.retcode}: {result.comment}")
                continue
        
        # If all filling modes failed, return the last error
        if result is None:
            return ExecutionResult(False, f"order_send returned None: {mt5.last_error()}")
        else:
            return ExecutionResult(False, f"Order failed retcode={result.retcode} comment={result.comment}")

    def close_position(self, order_id: int, close_type: str) -> ExecutionResult:
        """Close an existing position"""
        if not self.client.initialize():
            return ExecutionResult(False, "MT5 initialize failed")
        
        try:
            import MetaTrader5 as mt5
            
            # Get position info
            position = mt5.positions_get(ticket=order_id)
            if not position:
                return ExecutionResult(False, f"Position {order_id} not found")
            
            pos = position[0]
            symbol = pos.symbol
            volume = pos.volume
            
            # Determine close type
            if close_type == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
                price = self.client.tick(symbol).ask
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price = self.client.tick(symbol).bid
            
            # Create close request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "position": order_id,
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
            
            return ExecutionResult(True, f"Position {order_id} closed successfully", order_id=result.order)
            
        except Exception as e:
            return ExecutionResult(False, f"Error closing position: {e}")
    
    def modify_stop_loss(self, order_id: int, new_stop_loss: float) -> ExecutionResult:
        """Modify stop loss for an existing position"""
        if not self.client.initialize():
            return ExecutionResult(False, "MT5 initialize failed")
        
        try:
            import MetaTrader5 as mt5
            
            # Get position info
            position = mt5.positions_get(ticket=order_id)
            if not position:
                return ExecutionResult(False, f"Position {order_id} not found")
            
            pos = position[0]
            symbol = pos.symbol
            
            # Create modify request
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "sl": new_stop_loss,
                "tp": pos.tp,  # Keep existing take profit
                "position": order_id,
                "magic": int(self.magic_number),
                "comment": "Modify stop loss",
            }
            
            result = mt5.order_send(request)
            if result is None:
                return ExecutionResult(False, f"Modify SL failed: {mt5.last_error()}")
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return ExecutionResult(False, f"Modify SL failed retcode={result.retcode}: {result.comment}")
            
            return ExecutionResult(True, f"Stop loss modified to {new_stop_loss} for position {order_id}", order_id=result.order)
            
        except Exception as e:
            return ExecutionResult(False, f"Error modifying stop loss: {e}")

