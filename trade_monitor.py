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
    
    def should_close_for_loss_protection(self, market_data: Dict) -> tuple[bool, str]:
        """Determine if trade should be closed to prevent losses BEFORE stop loss"""
        if self.unrealized_pnl >= 0:
            return False, "Trade is profitable"
        
        # Loss protection criteria with PREDICTIVE elements
        loss_amount = abs(self.unrealized_pnl)
        entry_risk = abs(self.entry_price - self.stop_loss) * self.volume * 100000 * 0.00001 * 18.5
        
        # Get market data for prediction
        rsi = market_data.get('rsi', 50)
        volatility = market_data.get('volatility', 0)
        price_position = market_data.get('price_position', 0.5)
        resistance = market_data.get('resistance', 0)
        support = market_data.get('support', 0)
        
        # PREDICTIVE LOSS PREVENTION
        
        # 1. Accelerating loss detection (momentum)
        if self.duration_minutes > 10 and self.max_loss < 0:
            loss_acceleration = abs(self.unrealized_pnl - self.max_loss) / max(1, self.duration_minutes)
            if loss_acceleration > entry_risk * 0.02:  # Loss accelerating faster than 2% risk per minute
                return True, f"Accelerating losses detected (R{loss_amount:.2f})"
        
        # 2. Support/Resistance breach prediction
        if self.type == "BUY":
            # Buy trade - check if approaching support
            if support > 0 and self.current_price <= support * 1.002:  # Within 0.2% of support
                return True, f"Approaching support at {support:.5f} - predictive close"
        else:  # SELL trade
            # Sell trade - check if approaching resistance
            if resistance > 0 and self.current_price >= resistance * 0.998:  # Within 0.2% of resistance
                return True, f"Approaching resistance at {resistance:.5f} - predictive close"
        
        # 3. Volatility spike prediction (high volatility = higher loss probability)
        if volatility > 1.5 and loss_amount > entry_risk * 0.3:
            return True, f"High volatility with loss - predictive close (R{loss_amount:.2f})"
        
        # 4. RSI extreme prediction (continuation likely)
        if self.type == "BUY":
            # Buy trade - RSI showing strong bearish momentum
            if rsi < 25 and loss_amount > entry_risk * 0.25:
                return True, f"Strong bearish RSI ({rsi:.1f}) - predictive close"
        else:  # SELL trade
            # Sell trade - RSI showing strong bullish momentum
            if rsi > 75 and loss_amount > entry_risk * 0.25:
                return True, f"Strong bullish RSI ({rsi:.1f}) - predictive close"
        
        # 5. Time-based probability (longer losing trades less likely to recover)
        if self.duration_minutes > 30:
            recovery_probability = max(0, 1 - (self.duration_minutes - 30) * 0.02)  # 2% less recovery per minute after 30
            if recovery_probability < 0.3 and loss_amount > entry_risk * 0.4:
                return True, f"Low recovery probability ({recovery_probability:.1%}) - predictive close"
        
        # Traditional loss protection (fallback)
        
        # Close if loss exceeds 75% of initial risk
        if loss_amount > entry_risk * 0.75:
            return True, f"Loss exceeded 75% of risk (R{loss_amount:.2f})"
        
        # Close if trade is losing for more than 25 minutes and showing no recovery
        if self.duration_minutes > 25 and self.max_profit > 0:
            recovery_ratio = self.unrealized_pnl / self.max_profit if self.max_profit != 0 else 0
            if recovery_ratio < -0.4:  # Lost more than 40% of max profit
                return True, f"Trade failed to recover after {self.duration_minutes} minutes"
        
        # Close if trade is losing for more than 45 minutes
        if self.duration_minutes > 45:
            return True, f"Trade timeout after {self.duration_minutes} minutes"
        
        # Close if consecutive losses detected (price moving against trade)
        if self.duration_minutes > 20 and loss_amount > entry_risk * 0.35:
            # Check if price is consistently moving against trade direction
            price_trend_negative = (self.type == "BUY" and self.current_price < self.entry_price) or \
                                 (self.type == "SELL" and self.current_price > self.entry_price)
            if price_trend_negative:
                return True, f"Price moving against trade (R{loss_amount:.2f} loss)"
        
        return False, "No closure needed"
    
    def should_close_for_profit_protection(self, market_data: Dict) -> tuple[bool, str]:
        """Analyze market conditions and close profitable trades BEFORE they turn into losses"""
        if self.unrealized_pnl <= 0:
            return False, "Trade is not profitable"
        
        # Only consider profit protection for trades with meaningful profit
        profit_amount = self.unrealized_pnl
        entry_value = self.entry_price * self.volume * 100000
        profit_percent = (profit_amount / entry_value) * 100
        
        # Minimum profit threshold for protection (0.8% - allow more profit to build)
        if profit_percent < 0.8:
            return False, "Profit too small for protection"
        
        # Analyze market conditions
        market_condition = market_data.get('condition', 'UNKNOWN')
        current_signal = market_data.get('signal', Signal.HOLD)
        rsi = market_data.get('rsi', 50)
        price = market_data.get('price', 0)
        ema = market_data.get('ema', 0)
        
        # Profit protection criteria
        
        # 1. Signal reversal detected
        if self.type == "BUY" and current_signal == Signal.SELL:
            if profit_percent >= 1.2:  # At least 1.2% profit
                return True, f"Signal reversal to SELL - securing profit R{profit_amount:.2f}"
        elif self.type == "SELL" and current_signal == Signal.BUY:
            if profit_percent >= 1.2:
                return True, f"Signal reversal to BUY - securing profit R{profit_amount:.2f}"
        
        # 2. Market condition turning against trade
        if self.type == "BUY":
            # Buy trade protection
            if market_condition in ['OVERBOUGHT', 'DOWNTREND'] and profit_percent >= 1.0:
                return True, f"Market {market_condition} - securing profit R{profit_amount:.2f}"
            
            # RSI overbought and price below EMA (bearish signs)
            if rsi > 70 and price < ema and profit_percent >= 1.5:
                return True, f"RSI overbought with bearish price - securing profit R{profit_amount:.2f}"
                
            # Price approaching resistance
            if market_data.get('price_position', 0.5) > 0.85 and profit_percent >= 1.0:
                return True, f"Price near resistance - securing profit R{profit_amount:.2f}"
                
        else:  # SELL trade
            # Sell trade protection
            if market_condition in ['OVERSOLD', 'UPTREND'] and profit_percent >= 1.0:
                return True, f"Market {market_condition} - securing profit R{profit_amount:.2f}"
            
            # RSI oversold and price above EMA (bullish signs)
            if rsi < 30 and price > ema and profit_percent >= 1.5:
                return True, f"RSI oversold with bullish price - securing profit R{profit_amount:.2f}"
                
            # Price approaching support
            if market_data.get('price_position', 0.5) < 0.15 and profit_percent >= 1.0:
                return True, f"Price near support - securing profit R{profit_amount:.2f}"
        
        # 3. Profit fading detection
        if self.max_profit > 0:
            profit_fade_ratio = self.unrealized_pnl / self.max_profit
            if profit_fade_ratio < 0.6 and profit_percent >= 0.8:  # Lost more than 40% of max profit
                return True, f"Profit fading from R{self.max_profit:.2f} to R{profit_amount:.2f}"
        
        # 4. Time-based profit protection (for trades running too long)
        if self.duration_minutes > 60 and profit_percent >= 0.5:  # 1 hour with profit
            return True, f"Long profitable trade - securing R{profit_amount:.2f} after {self.duration_minutes} minutes"
        
        # 5. Volatility spike protection
        if market_data.get('volatility', 0) > 2.0 and profit_percent >= 0.6:  # High volatility
            return True, f"High volatility detected - securing profit R{profit_amount:.2f}"
        
        return False, "No profit protection needed"
    
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
            
            # Get additional market data for profit protection
            volatility = float(last.get('volatility', 0))
            price_position = float(last.get('price_position', 0.5))
            resistance = float(last.get('resistance', price * 1.02))
            support = float(last.get('support', price * 0.98))
            
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
                'ema': ema,
                'volatility': volatility,
                'price_position': price_position,
                'resistance': resistance,
                'support': support
            }
            
        except Exception as e:
            logger.error(f"Error getting market analysis: {e}")
            return {'condition': 'ERROR', 'signal': Signal.HOLD, 'rsi': 50, 'price': 0, 'ema': 0}
    
    def _analyze_trade(self, trade: OpenTrade, market_data: Dict) -> TradeDecision:
        """Analyze a trade and make a decision with enhanced loss and profit protection"""
        
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
        
        # PROACTIVE PROFIT PROTECTION - Check first for profitable trades
        if trade.unrealized_pnl > 0:
            should_close, reason = trade.should_close_for_profit_protection(market_data)
            if should_close:
                return TradeDecision("CLOSE", reason, confidence=0.85)
        
        # Enhanced loss protection - check for losing trades with predictive analysis
        should_close, reason = trade.should_close_for_loss_protection(market_data)
        if should_close:
            return TradeDecision("CLOSE", reason, confidence=0.9)
        
        # Check minimum duration
        if trade.duration_minutes < self.min_trade_duration_minutes:
            return TradeDecision("HOLD", f"Too early (only {trade.duration_minutes} minutes)", confidence=0.8)
        
        # Check maximum duration (increased to 45 minutes to match loss protection)
        if trade.duration_minutes > 45:
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
