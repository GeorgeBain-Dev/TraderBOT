"""
Signal Performance Tracker
Tracks performance of different signal types to identify which work best
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import json
import os

from utils import setup_logging

logger = setup_logging()


@dataclass
class SignalPerformance:
    """Performance metrics for a specific signal type"""
    signal_type: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: float = 0.0
    total_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_profit_per_trade: float = 0.0
    last_updated: str = ""
    
    def calculate_metrics(self) -> None:
        """Calculate performance metrics"""
        if self.total_trades > 0:
            self.win_rate = (self.winning_trades / self.total_trades) * 100
            self.avg_profit_per_trade = (self.total_profit - self.total_loss) / self.total_trades
            if self.total_loss > 0:
                self.profit_factor = self.total_profit / self.total_loss
            else:
                self.profit_factor = float('inf') if self.total_profit > 0 else 0.0
        self.last_updated = datetime.utcnow().isoformat()
    
    def is_proven(self, min_trades: int = 10, min_win_rate: float = 45.0, min_profit_factor: float = 1.1) -> bool:
        """Check if signal has proven performance"""
        return (
            self.total_trades >= min_trades
            and self.win_rate >= min_win_rate
            and self.profit_factor >= min_profit_factor
        )


class SignalTracker:
    """Tracks performance of different signal types"""
    
    def __init__(self, data_dir: str = "logs"):
        self.data_dir = data_dir
        self.signals: Dict[str, SignalPerformance] = {}
        self.data_file = os.path.join(data_dir, "signal_performance.json")
        self._load_data()
    
    def _load_data(self) -> None:
        """Load signal performance data from file"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    for signal_type, perf_data in data.items():
                        self.signals[signal_type] = SignalPerformance(
                            signal_type=signal_type,
                            total_trades=perf_data.get('total_trades', 0),
                            winning_trades=perf_data.get('winning_trades', 0),
                            losing_trades=perf_data.get('losing_trades', 0),
                            total_profit=perf_data.get('total_profit', 0.0),
                            total_loss=perf_data.get('total_loss', 0.0),
                            win_rate=perf_data.get('win_rate', 0.0),
                            profit_factor=perf_data.get('profit_factor', 0.0),
                            avg_profit_per_trade=perf_data.get('avg_profit_per_trade', 0.0),
                            last_updated=perf_data.get('last_updated', ''),
                        )
                logger.info(f"Loaded signal performance data for {len(self.signals)} signal types")
        except Exception as e:
            logger.warning(f"Failed to load signal performance data: {e}")
    
    def _save_data(self) -> None:
        """Save signal performance data to file"""
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            data = {}
            for signal_type, perf in self.signals.items():
                data[signal_type] = {
                    'total_trades': perf.total_trades,
                    'winning_trades': perf.winning_trades,
                    'losing_trades': perf.losing_trades,
                    'total_profit': perf.total_profit,
                    'total_loss': perf.total_loss,
                    'win_rate': perf.win_rate,
                    'profit_factor': perf.profit_factor,
                    'avg_profit_per_trade': perf.avg_profit_per_trade,
                    'last_updated': perf.last_updated,
                }
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save signal performance data: {e}")
    
    def record_signal(self, signal_type: str, reasons: List[str]) -> None:
        """Record that a signal was generated"""
        if signal_type not in self.signals:
            self.signals[signal_type] = SignalPerformance(signal_type=signal_type)
        
        # Track individual reasons as separate signal types
        for reason in reasons:
            if reason not in self.signals:
                self.signals[reason] = SignalPerformance(signal_type=reason)
    
    def record_trade_outcome(self, signal_type: str, profit: float) -> None:
        """Record the outcome of a trade for a specific signal"""
        if signal_type not in self.signals:
            self.signals[signal_type] = SignalPerformance(signal_type=signal_type)
        
        perf = self.signals[signal_type]
        perf.total_trades += 1
        
        if profit > 0:
            perf.winning_trades += 1
            perf.total_profit += profit
        else:
            perf.losing_trades += 1
            perf.total_loss += abs(profit)
        
        perf.calculate_metrics()
        self._save_data()
        logger.info(f"Recorded trade outcome for {signal_type}: profit={profit:.2f}, win_rate={perf.win_rate:.1f}%")
    
    def is_signal_proven(self, signal_type: str, min_trades: int = 10, min_win_rate: float = 45.0, min_profit_factor: float = 1.1) -> bool:
        """Check if a signal type has proven performance"""
        if signal_type not in self.signals:
            return False
        return self.signals[signal_type].is_proven(min_trades, min_win_rate, min_profit_factor)
    
    def get_proven_signals(self, min_trades: int = 10, min_win_rate: float = 45.0, min_profit_factor: float = 1.1) -> List[str]:
        """Get list of proven signal types"""
        proven = []
        for signal_type, perf in self.signals.items():
            if perf.is_proven(min_trades, min_win_rate, min_profit_factor):
                proven.append(signal_type)
        return proven
    
    def get_signal_performance(self, signal_type: str) -> Optional[SignalPerformance]:
        """Get performance data for a specific signal"""
        return self.signals.get(signal_type)
    
    def get_all_performance(self) -> Dict[str, SignalPerformance]:
        """Get performance data for all signals"""
        return self.signals.copy()
    
    def print_performance_report(self) -> None:
        """Print a performance report for all signals"""
        if not self.signals:
            logger.info("No signal performance data available")
            return
        
        logger.info("\n" + "="*80)
        logger.info("SIGNAL PERFORMANCE REPORT")
        logger.info("="*80)
        
        for signal_type, perf in sorted(self.signals.items(), key=lambda x: x[1].total_trades, reverse=True):
            logger.info(
                f"{signal_type:30s} | Trades: {perf.total_trades:3d} | "
                f"Win Rate: {perf.win_rate:5.1f}% | PF: {perf.profit_factor:5.2f} | "
                f"Avg Profit: {perf.avg_profit_per_trade:8.2f}"
            )
        
        logger.info("="*80)
