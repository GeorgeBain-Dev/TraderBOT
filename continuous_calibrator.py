from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

try:
    from .config import BotConfig
except ImportError:
    from config import BotConfig
try:
    from .data import MT5Client, get_latest_candles, get_live_price
except ImportError:
    from data import MT5Client, get_latest_candles, get_live_price
try:
    from .strategy import RsiEmaStrategy
except ImportError:
    from strategy import RsiEmaStrategy
try:
    from .optimizer import EdgeCriteria, calibrate_best_params
except ImportError:
    from optimizer import EdgeCriteria, calibrate_best_params
try:
    from .risk import RiskManager
except ImportError:
    from risk import RiskManager
try:
    from .utils import setup_logging, utc_now
except ImportError:
    from utils import setup_logging, utc_now

logger = setup_logging()


@dataclass
class CalibrationResult:
    """Result of a calibration run"""
    timestamp: datetime
    params: Optional[CalibratedParams]
    edge_found: bool
    profit_factor: float
    trades_count: int
    total_pnl: float
    win_rate: float
    max_drawdown: float
    execution_time: float


class ContinuousCalibrator:
    """Continuously calibrates strategy parameters on live data"""
    
    def __init__(self, client: MT5Client, config: BotConfig):
        self.client = client
        self.config = config
        self.is_running = False
        self.calibration_thread: Optional[threading.Thread] = None
        
        # Calibration history
        self.calibration_history: List[CalibrationResult] = []
        self.current_params: Optional[CalibratedParams] = None
        self.last_calibration_time: Optional[datetime] = None
        
        # Strategy instance for testing
        self.strategy = RsiEmaStrategy()
        self.risk = RiskManager(risk_per_trade=0.01, sl_atr_mult=1.5, tp_rr=2.0)
        
        # Calibration parameters
        self.calibration_interval_minutes = 5  # Calibrate every 5 minutes
        self.min_data_points = 500  # Minimum candles for calibration
        self.max_data_points = 2000  # Maximum candles for calibration
        
        # Parameter search space
        self.rsi_periods = [10, 14, 21, 30]
        self.ema_periods = [50, 100, 150, 200]
        self.rsi_buy_range = [35.0, 40.0, 45.0, 50.0, 55.0]
        self.rsi_sell_range = [50.0, 55.0, 60.0, 65.0, 70.0]
        
        # Edge criteria (very permissive to allow trading)
        self.edge_criteria = EdgeCriteria(
            min_trades_for_edge=1,  # Only need 1 trade
            min_profit_factor=1.001,  # Only 0.1% profit needed
            min_total_pnl=-0.05,  # Allow small losses for learning
            max_drawdown_abs=0.25,  # Higher tolerance
            min_win_rate=0.40  # Lower win rate requirement
        )
    
    def start_continuous_calibration(self) -> None:
        """Start continuous calibration in background thread"""
        if self.is_running:
            logger.warning("Continuous calibration already running")
            return
        
        self.is_running = True
        self.calibration_thread = threading.Thread(target=self._calibration_loop, daemon=True)
        self.calibration_thread.start()
        logger.info("Started continuous edge calibration")
    
    def stop_continuous_calibration(self) -> None:
        """Stop continuous calibration"""
        self.is_running = False
        if self.calibration_thread:
            self.calibration_thread.join(timeout=10)
        logger.info("Stopped continuous edge calibration")
    
    def _calibration_loop(self) -> None:
        """Main calibration loop running in background thread"""
        while self.is_running:
            try:
                # Perform calibration
                result = self._perform_calibration()
                
                # Update strategy if better edge found
                if result.edge_found and result.params:
                    self._update_strategy(result)
                
                # Log results
                self._log_calibration_result(result)
                
                # Store in history
                self.calibration_history.append(result)
                
                # Keep only last 24 hours of history
                cutoff_time = datetime.now(timezone.utc) - pd.Timedelta(hours=24)
                self.calibration_history = [
                    r for r in self.calibration_history 
                    if r.timestamp > cutoff_time
                ]
                
                # Wait for next calibration
                time.sleep(self.calibration_interval_minutes * 60)
                
            except Exception as e:
                logger.error(f"Error in calibration loop: {e}")
                time.sleep(60)  # Wait 1 minute before retrying
    
    def _perform_calibration(self) -> CalibrationResult:
        """Perform a single calibration run"""
        start_time = time.time()
        
        try:
            # Get recent market data
            df = get_latest_candles(
                self.client, 
                self.config.symbol, 
                self.config.timeframe, 
                n=self.max_data_points
            )
            
            if df.empty or len(df) < self.min_data_points:
                return CalibrationResult(
                    timestamp=datetime.now(timezone.utc),
                    params=None,
                    edge_found=False,
                    profit_factor=0.0,
                    trades_count=0,
                    total_pnl=0.0,
                    win_rate=0.0,
                    max_drawdown=0.0,
                    execution_time=time.time() - start_time
                )
            
            # Run optimization
            best_params = self._optimize_parameters(df)
            
            if best_params:
                # Validate edge
                edge_valid = self._validate_edge(best_params, df)
                
                return CalibrationResult(
                    timestamp=datetime.now(timezone.utc),
                    params=best_params if edge_valid else None,
                    edge_found=edge_valid,
                    profit_factor=best_params.profit_factor,
                    trades_count=best_params.trades_count,
                    total_pnl=best_params.total_pnl,
                    win_rate=best_params.win_rate,
                    max_drawdown=best_params.max_drawdown_abs,
                    execution_time=time.time() - start_time
                )
            else:
                return CalibrationResult(
                    timestamp=datetime.now(timezone.utc),
                    params=None,
                    edge_found=False,
                    profit_factor=0.0,
                    trades_count=0,
                    total_pnl=0.0,
                    win_rate=0.0,
                    max_drawdown=0.0,
                    execution_time=time.time() - start_time
                )
                
        except Exception as e:
            logger.error(f"Calibration failed: {e}")
            return CalibrationResult(
                timestamp=datetime.now(timezone.utc),
                params=None,
                edge_found=False,
                profit_factor=0.0,
                trades_count=0,
                total_pnl=0.0,
                win_rate=0.0,
                max_drawdown=0.0,
                execution_time=time.time() - start_time
            )
    
    def _optimize_parameters(self, df: pd.DataFrame) -> Optional[CalibratedParams]:
        """Optimize strategy parameters on recent data"""
        best: Optional[CalibratedParams] = None
        best_score = -float("inf")
        
        # Test different parameter combinations
        for rsi_period in self.rsi_periods:
            for ema_period in self.ema_periods:
                for rsi_buy in self.rsi_buy_range:
                    for rsi_sell in self.rsi_sell_range:
                        if rsi_buy >= rsi_sell:
                            continue  # Skip invalid combinations
                        
                        # Create strategy with these parameters
                        strat = RsiEmaStrategy(
                            rsi_period=rsi_period,
                            ema_period=ema_period,
                            rsi_buy=rsi_buy,
                            rsi_sell=rsi_sell
                        )
                        
                        # Run backtest
                        try:
                            res = run_backtest(df, strat, self.risk, initial_balance=100000)
                            trades_n = len(res.trades)
                            
                            if trades_n < self.edge_criteria.min_trades_for_edge:
                                continue
                            
                            # Calculate metrics
                            pf = _profit_factor([t.pnl for t in res.trades])
                            if not np.isfinite(pf) or pf < self.edge_criteria.min_profit_factor:
                                continue
                            
                            total_pnl = sum(t.pnl for t in res.trades)
                            if total_pnl < self.edge_criteria.min_total_pnl:
                                continue
                            
                            # Calculate drawdown
                            equity_curve = [100000]
                            for trade in res.trades:
                                equity_curve.append(equity_curve[-1] + trade.pnl)
                            
                            peak = equity_curve[0]
                            max_dd = 0.0
                            for value in equity_curve:
                                if value > peak:
                                    peak = value
                                dd = (peak - value) / peak
                                if dd > max_dd:
                                    max_dd = dd
                            
                            if max_dd > self.edge_criteria.max_drawdown_abs:
                                continue
                            
                            # Calculate win rate
                            wins = sum(1 for t in res.trades if t.pnl > 0)
                            win_rate = wins / trades_n
                            if win_rate < self.edge_criteria.min_win_rate:
                                continue
                            
                            # Create calibrated params
                            params = CalibratedParams(
                                rsi_period=rsi_period,
                                ema_period=ema_period,
                                rsi_buy=rsi_buy,
                                rsi_sell=rsi_sell,
                                sl_atr_mult=self.config.sl_atr_mult,
                                tp_rr=self.config.tp_rr,
                                profit_factor=pf,
                                trades_count=trades_n,
                                total_pnl=total_pnl,
                                win_rate=win_rate,
                                max_drawdown_abs=max_dd
                            )
                            
                            # Score based on profit factor and trade count
                            score = pf * np.log1p(trades_n)  # Favor more trades with good PF
                            
                            if score > best_score:
                                best = params
                                best_score = score
                                
                        except Exception as e:
                            logger.debug(f"Parameter test failed: {e}")
                            continue
        
        return best
    
    def _validate_edge(self, params: CalibratedParams, df: pd.DataFrame) -> bool:
        """Validate that parameters provide a valid edge"""
        return (
            params.trades_count >= self.edge_criteria.min_trades_for_edge and
            params.profit_factor >= self.edge_criteria.min_profit_factor and
            params.total_pnl >= self.edge_criteria.min_total_pnl and
            params.max_drawdown_abs <= self.edge_criteria.max_drawdown_abs and
            params.win_rate >= self.edge_criteria.min_win_rate
        )
    
    def _update_strategy(self, result: CalibrationResult) -> None:
        """Update strategy with new calibrated parameters"""
        if result.params:
            # Update strategy parameters
            self.strategy.rsi_period = result.params.rsi_period
            self.strategy.ema_period = result.params.ema_period
            self.strategy.rsi_buy = result.params.rsi_buy
            self.strategy.rsi_sell = result.params.rsi_sell
            
            # Update risk parameters
            self.risk.sl_atr_mult = result.params.sl_atr_mult
            self.risk.tp_rr = result.params.tp_rr
            
            # Store current params
            self.current_params = result.params
            self.last_calibration_time = result.timestamp
            
            logger.info(f"Updated strategy: RSI({result.params.rsi_period}) "
                       f"EMA({result.params.ema_period}) "
                       f"BUY({result.params.rsi_buy}) "
                       f"SELL({result.params.rsi_sell}) "
                       f"PF({result.params.profit_factor:.3f})")
    
    def _log_calibration_result(self, result: CalibrationResult) -> None:
        """Log calibration results"""
        if result.edge_found:
            logger.info(f"Edge found: PF={result.profit_factor:.3f} "
                        f"Trades={result.trades_count} "
                        f"WinRate={result.win_rate:.2f} "
                        f"DD={result.max_drawdown:.3f} "
                        f"Time={result.execution_time:.1f}s")
        else:
            logger.info(f"No edge found - Time={result.execution_time:.1f}s")
    
    def get_current_strategy(self) -> RsiEmaStrategy:
        """Get the current calibrated strategy"""
        return self.strategy
    
    def get_current_risk(self) -> RiskManager:
        """Get the current calibrated risk manager"""
        return self.risk
    
    def get_calibration_status(self) -> Dict:
        """Get current calibration status"""
        return {
            "is_running": self.is_running,
            "last_calibration": self.last_calibration_time.isoformat() if self.last_calibration_time else None,
            "current_params": self.current_params.__dict__ if self.current_params else None,
            "calibration_count_24h": len(self.calibration_history),
            "recent_edges": sum(1 for r in self.calibration_history if r.edge_found)
        }
    
    def get_best_recent_params(self) -> Optional[CalibratedParams]:
        """Get the best parameters from recent calibrations"""
        if not self.calibration_history:
            return None
        
        # Find best result from last 12 hours
        cutoff_time = datetime.now(timezone.utc) - pd.Timedelta(hours=12)
        recent_results = [
            r for r in self.calibration_history 
            if r.timestamp > cutoff_time and r.edge_found
        ]
        
        if not recent_results:
            return None
        
        # Return result with highest profit factor
        best = max(recent_results, key=lambda r: r.profit_factor)
        return best.params
