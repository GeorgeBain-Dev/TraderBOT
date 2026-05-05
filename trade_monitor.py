from __future__ import annotations

from dataclasses import dataclass

try:
    from .data import MT5Client, MT5Error, get_latest_candles
except ImportError:
    from data import MT5Client, MT5Error, get_latest_candles
try:
    from .execution import ExecutionEngine, ExecutionResult
except ImportError:
    from execution import ExecutionEngine, ExecutionResult
try:
    from .risk import RiskManager, TradePlan
except ImportError:
    from risk import RiskManager, TradePlan
try:
    from .strategy import RsiEmaStrategy
except ImportError:
    from strategy import RsiEmaStrategy
try:
    from .utils import setup_logging
except ImportError:
    from utils import setup_logging

logger = setup_logging()


@dataclass
class OpenTrade:
    """Represents an open trade with all tracking data"""
    order_id: int
    symbol: str
    type: str  # "BUY" or "SELL"
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: datetime
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    duration_minutes: int = 0
    trailing_stop: Optional[float] = None
    last_signal: Optional[Signal] = None
    market_condition: str = "UNKNOWN"
    
    def update_price(self, current_price: float) -> None:
        """Update trade with current price and calculate P&L"""
        self.current_price = current_price
        self.duration_minutes = int((datetime.now(timezone.utc) - self.entry_time).total_seconds() / 60)
        
        if self.type == "BUY":
            self.unrealized_pnl = (current_price - self.entry_price) * self.volume * 100000 * 0.00001 * 18.5  # ZAR calculation
        else:  # SELL
            self.unrealized_pnl = (self.entry_price - current_price) * self.volume * 100000 * 0.00001 * 18.5
        
        # Track max profit/loss
        self.max_profit = max(self.max_profit, self.unrealized_pnl)
        self.max_loss = min(self.max_loss, self.unrealized_pnl)
    
    def should_close_for_loss_protection(self) -> tuple[bool, str]:
        """Determine if trade should be closed to prevent losses BEFORE stop loss"""
        if self.unrealized_pnl >= 0:
            return False, "Trade is profitable"
        
        # Loss protection criteria - MORE AGGRESSIVE
        loss_amount = abs(self.unrealized_pnl)
        entry_risk = abs(self.entry_price - self.stop_loss) * self.volume * 100000 * 0.00001 * 18.5
        
        # Close if loss exceeds 50% of initial risk (more aggressive)
        if loss_amount > entry_risk * 0.50:
            return True, f"Loss exceeded 50% of risk (R{loss_amount:.2f})"
        
        # Close if trade is losing for more than 15 minutes and showing no recovery
        if self.duration_minutes > 15 and self.max_profit > 0:
            recovery_ratio = self.unrealized_pnl / self.max_profit if self.max_profit != 0 else 0
            if recovery_ratio < -0.3:  # Lost more than 30% of max profit
                return True, f"Trade failed to recover after {self.duration_minutes} minutes"
        
        # Close if trade is losing for more than 30 minutes
        if self.duration_minutes > 30:
            return True, f"Trade timeout after {self.duration_minutes} minutes"
        
        # Close if consecutive losses detected (price moving against trade)
        if self.duration_minutes > 10 and loss_amount > entry_risk * 0.25:
            # Check if price is consistently moving against trade direction
            price_trend = "negative" if (self.type == "BUY" and self.current_price < self.entry_price) else "negative"
            if price_trend == "negative":
                return True, f"Price moving against trade (R{loss_amount:.2f} loss)"
        
        return False, "No closure needed"
    
    def get_trailing_stop_price(self) -> float:
        """Calculate trailing stop price based on max profit"""
        if self.max_profit <= 0:
            return self.stop_loss
        
        # Trail stop by 50% of max profit
        trail_amount = self.max_profit * 0.5
        
        if self.type == "BUY":
            return max(self.stop_loss, self.entry_price + trail_amount)
        else:  # SELL
            return min(self.stop_loss, self.entry_price - trail_amount)


@dataclass
class TradeDecision:
    """Represents a decision about an open trade"""
    action: str  # "CLOSE", "MODIFY_SL", "HOLD"
    reason: str
    new_stop_loss: Optional[float] = None
    confidence: float = 0.0


class TradeMonitor:
    """Monitors open trades and makes intelligent management decisions"""
    
    def __init__(self, client: MT5Client, execution_engine: ExecutionEngine):
        self.client = client
        self.execution = execution_engine
        self.open_trades: Dict[int, OpenTrade] = {}
        self.strategy = RsiEmaStrategy()
        self.strategy.update_timeframe("M1")  # Use M1 for trade monitoring
        
        # Trade management parameters
        self.trailing_stop_percent = 0.5  # 0.5% for trailing stop
        self.max_profit_target_percent = 2.0  # 2% profit target
        self.max_loss_percent = 1.5  # 1.5% max loss
        self.min_trade_duration_minutes = 5  # Minimum 5 minutes before considering closure
        self.max_trade_duration_minutes = 240  # Maximum 4 hours per trade
        
    def add_trade(self, order_id: int, symbol: str, trade_type: str, volume: float,
                  entry_price: float, stop_loss: float, take_profit: float) -> None:
        """Add a new trade to monitor"""
        trade = OpenTrade(
            order_id=order_id,
            symbol=symbol,
            type=trade_type,
            volume=volume,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=datetime.now(timezone.utc)
        )
        self.open_trades[order_id] = trade
        logger.info(f"Added trade to monitor: {trade_type} {symbol} @ {entry_price}")
    
    def remove_trade(self, order_id: int) -> None:
        """Remove a trade from monitoring"""
        if order_id in self.open_trades:
            trade = self.open_trades[order_id]
            logger.info(f"Removed trade from monitor: {trade.type} {trade.symbol} - P&L: R{trade.unrealized_pnl:.2f}")
            del self.open_trades[order_id]
    
    def update_all_trades(self) -> List[Tuple[int, TradeDecision]]:
        """Update all open trades with current data and return decisions"""
        decisions = []
        
        for order_id, trade in list(self.open_trades.items()):
            try:
                # Get current price
                tick = self.client.tick(trade.symbol)
                current_price = float(tick.bid if trade.type == "SELL" else tick.ask)
                
                # Update trade data
                trade.update_price(current_price)
                
                # Get market analysis
                market_data = self._get_market_analysis(trade.symbol)
                trade.market_condition = market_data['condition']
                trade.last_signal = market_data['signal']
                
                # Make decision
                decision = self._analyze_trade(trade, market_data)
                decisions.append((order_id, decision))
                
            except Exception as e:
                logger.error(f"Error updating trade {order_id}: {e}")
                decisions.append((order_id, TradeDecision("HOLD", f"Error: {e}")))
        
        return decisions
    
    def _get_market_analysis(self, symbol: str) -> Dict:
        """Get current market analysis for a symbol"""
        try:
            from .data import get_latest_candles
            from .config import BotConfig
            
            config = BotConfig()
            df = get_latest_candles(self.client, symbol, config.timeframe, n=100)
            
            if df.empty:
                return {'condition': 'NO_DATA', 'signal': Signal.HOLD, 'rsi': 50, 'price': 0, 'ema': 0}
            
            df_prepared = self.strategy.prepare(df)
            signal = self.strategy.generate_signal(df_prepared)
            
            last = df_prepared.iloc[-1]
            rsi = float(last['rsi'])
            price = float(last['close'])
            ema = float(last['ema'])
            
            # Determine market condition
            if rsi < 35:
                condition = 'OVERSOLD'
            elif rsi > 65:
                condition = 'OVERBOUGHT'
            elif price > ema:
                condition = 'UPTREND'
            elif price < ema:
                condition = 'DOWNTREND'
            else:
                condition = 'SIDEWAYS'
            
            return {
                'condition': condition,
                'signal': signal,
                'rsi': rsi,
                'price': price,
                'ema': ema
            }
            
        except Exception as e:
            logger.error(f"Error getting market analysis: {e}")
            return {'condition': 'ERROR', 'signal': Signal.HOLD, 'rsi': 50, 'price': 0, 'ema': 0}
    
    def _analyze_trade(self, trade: OpenTrade, market_data: Dict) -> TradeDecision:
        """Analyze a trade and make a decision with enhanced loss protection"""
        
        # Check if trade should be closed due to stop loss or take profit
        if trade.type == "BUY":
            if trade.current_price <= trade.stop_loss:
                return TradeDecision("CLOSE", "Stop loss hit", confidence=1.0)
            elif trade.current_price >= trade.take_profit:
                return TradeDecision("CLOSE", "Take profit hit", confidence=1.0)
        else:  # SELL
            if trade.current_price >= trade.stop_loss:
                return TradeDecision("CLOSE", "Stop loss hit", confidence=1.0)
            elif trade.current_price <= trade.take_profit:
                return TradeDecision("CLOSE", "Take profit hit", confidence=1.0)
        
        # Enhanced loss protection - check first
        should_close, reason = trade.should_close_for_loss_protection()
        if should_close:
            return TradeDecision("CLOSE", reason, confidence=0.9)
        
        # Check minimum duration
        if trade.duration_minutes < self.min_trade_duration_minutes:
            return TradeDecision("HOLD", f"Too early (only {trade.duration_minutes} minutes)", confidence=0.8)
        
        # Check maximum duration (reduced to 30 minutes)
        if trade.duration_minutes > 30:
            return TradeDecision("CLOSE", f"Maximum duration exceeded ({trade.duration_minutes} minutes)", confidence=0.7)
        
        # Analyze based on market conditions
        decision = self._analyze_by_market_condition(trade, market_data)
        if decision.action != "HOLD":
            return decision
        
        # Analyze based on profit/loss
        decision = self._analyze_by_profit_loss(trade)
        if decision.action != "HOLD":
            return decision
        
        # Analyze based on trailing stop
        decision = self._analyze_trailing_stop(trade)
        if decision.action != "HOLD":
            return decision
        
        # Analyze based on signal reversal
        decision = self._analyze_signal_reversal(trade, market_data)
        if decision.action != "HOLD":
            return decision
        
        return TradeDecision("HOLD", "No reason to close", confidence=0.5)
    
    def _analyze_by_market_condition(self, trade: OpenTrade, market_data: Dict) -> TradeDecision:
        """Analyze trade based on market conditions"""
        
        # BUY trade analysis
        if trade.type == "BUY":
            if market_data['condition'] == 'OVERBOUGHT' and trade.unrealized_pnl > 0:
                return TradeDecision("CLOSE", "Market overbought with profit", confidence=0.7)
            elif market_data['condition'] == 'DOWNTREND' and trade.unrealized_pnl < 0:
                return TradeDecision("CLOSE", "Market trending down with loss", confidence=0.8)
            elif market_data['signal'] == Signal.SELL:
                return TradeDecision("CLOSE", "Signal reversal to SELL", confidence=0.8)
        
        # SELL trade analysis
        else:
            if market_data['condition'] == 'OVERSOLD' and trade.unrealized_pnl > 0:
                return TradeDecision("CLOSE", "Market oversold with profit", confidence=0.7)
            elif market_data['condition'] == 'UPTREND' and trade.unrealized_pnl < 0:
                return TradeDecision("CLOSE", "Market trending up with loss", confidence=0.8)
            elif market_data['signal'] == Signal.BUY:
                return TradeDecision("CLOSE", "Signal reversal to BUY", confidence=0.8)
        
        return TradeDecision("HOLD", "Market condition doesn't warrant closure", confidence=0.5)
    
    def _analyze_by_profit_loss(self, trade: OpenTrade) -> TradeDecision:
        """Analyze trade based on profit/loss levels"""
        
        # Calculate profit/loss percentage
        entry_value = trade.entry_price * trade.volume * 100000
        pnl_percent = (trade.unrealized_pnl / entry_value) * 100
        
        # Close if profit target reached
        if pnl_percent >= self.max_profit_target_percent:
            return TradeDecision("CLOSE", f"Profit target reached ({pnl_percent:.1f}%)", confidence=0.9)
        
        # Close if loss exceeds maximum
        if pnl_percent <= -self.max_loss_percent:
            return TradeDecision("CLOSE", f"Maximum loss exceeded ({pnl_percent:.1f}%)", confidence=0.9)
        
        # Consider closing if profit is fading
        if trade.max_profit > 0 and trade.unrealized_pnl < trade.max_profit * 0.5:
            return TradeDecision("CLOSE", f"Profit faded from R{trade.max_profit:.2f} to R{trade.unrealized_pnl:.2f}", confidence=0.6)
        
        return TradeDecision("HOLD", "Profit/loss within acceptable range", confidence=0.5)
    
    def _analyze_trailing_stop(self, trade: OpenTrade) -> TradeDecision:
        """Analyze trade for trailing stop adjustments"""
        
        if trade.unrealized_pnl > 0:
            # Calculate trailing stop level
            trailing_distance = trade.entry_price * (self.trailing_stop_percent / 100)
            
            if trade.type == "BUY":
                new_stop = trade.current_price - trailing_distance
                if new_stop > trade.stop_loss:
                    # Move stop loss up
                    return TradeDecision("MODIFY_SL", f"Trailing stop to {new_stop:.5f}", 
                                      new_stop_loss=new_stop, confidence=0.8)
            else:  # SELL
                new_stop = trade.current_price + trailing_distance
                if new_stop < trade.stop_loss:
                    # Move stop loss down
                    return TradeDecision("MODIFY_SL", f"Trailing stop to {new_stop:.5f}", 
                                      new_stop_loss=new_stop, confidence=0.8)
        
        return TradeDecision("HOLD", "No trailing stop adjustment needed", confidence=0.5)
    
    def _analyze_signal_reversal(self, trade: OpenTrade, market_data: Dict) -> TradeDecision:
        """Analyze trade based on signal reversals"""
        
        # If we have a strong reversal signal and the trade is losing
        if trade.unrealized_pnl < 0:
            if trade.type == "BUY" and market_data['signal'] == Signal.SELL:
                return TradeDecision("CLOSE", "Strong SELL signal reversal", confidence=0.7)
            elif trade.type == "SELL" and market_data['signal'] == Signal.BUY:
                return TradeDecision("CLOSE", "Strong BUY signal reversal", confidence=0.7)
        
        return TradeDecision("HOLD", "No strong signal reversal", confidence=0.5)
    
    def execute_decision(self, order_id: int, decision: TradeDecision) -> bool:
        """Execute a trade decision"""
        try:
            if order_id not in self.open_trades:
                logger.error(f"Trade {order_id} not found for execution")
                return False
            
            trade = self.open_trades[order_id]
            
            if decision.action == "CLOSE":
                # Close the position
                if trade.type == "BUY":
                    result = self.execution.close_position(order_id, "SELL")
                else:
                    result = self.execution.close_position(order_id, "BUY")
                
                if result.ok:
                    logger.info(f"Closed trade {order_id}: {decision.reason} - P&L: R{trade.unrealized_pnl:.2f}")
                    self.remove_trade(order_id)
                    return True
                else:
                    logger.error(f"Failed to close trade {order_id}: {result.message}")
                    return False
            
            elif decision.action == "MODIFY_SL":
                # Modify stop loss
                result = self.execution.modify_stop_loss(order_id, decision.new_stop_loss)
                if result.ok:
                    trade.stop_loss = decision.new_stop_loss
                    logger.info(f"Modified stop loss for trade {order_id}: {decision.reason}")
                    return True
                else:
                    logger.error(f"Failed to modify stop loss for trade {order_id}: {result.message}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error executing decision for trade {order_id}: {e}")
            return False
    
    def get_trade_summary(self) -> Dict:
        """Get summary of all open trades"""
        if not self.open_trades:
            return {
                'total_trades': 0,
                'total_pnl': 0.0,
                'trades': []
            }
        
        trades = []
        total_pnl = 0.0
        
        for trade in self.open_trades.values():
            trades.append({
                'order_id': trade.order_id,
                'symbol': trade.symbol,
                'type': trade.type,
                'volume': trade.volume,
                'entry_price': trade.entry_price,
                'current_price': trade.current_price,
                'unrealized_pnl': trade.unrealized_pnl,
                'duration_minutes': trade.duration_minutes,
                'market_condition': trade.market_condition,
                'last_signal': trade.last_signal.name if trade.last_signal else 'NONE'
            })
            total_pnl += trade.unrealized_pnl
        
        return {
            'total_trades': len(self.open_trades),
            'total_pnl': total_pnl,
            'trades': trades
        }
