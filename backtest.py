from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from .risk import RiskManager
except ImportError:
    from risk import RiskManager
try:
    from .strategy import BaseStrategy, Signal
except ImportError:
    from strategy import BaseStrategy, Signal
try:
    from .utils import setup_logging
except ImportError:
    from utils import setup_logging

logger = setup_logging()


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    entry: float
    exit: float
    sl: float
    tp: float
    pnl: float
    win: bool


@dataclass
class BacktestResult:
    trades: List[Trade]
    total_pnl: float
    win_rate: float
    max_drawdown: float
    equity_curve: pd.Series
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    sharpe_ratio: float = 0.0
    trade_frequency: float = 0.0  # trades per day


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak)
    return float(dd.min())


def run_backtest(
    df: pd.DataFrame,
    strategy: BaseStrategy,
    risk: RiskManager,
    initial_balance: float = 10_000.0,
) -> BacktestResult:
    if df.empty:
        return BacktestResult([], 0.0, 0.0, 0.0, pd.Series(dtype=float))

    data = strategy.prepare(df)
    data = data.dropna().copy()
    if data.empty or len(data) < 50:
        return BacktestResult([], 0.0, 0.0, 0.0, pd.Series(dtype=float))

    balance = float(initial_balance)
    equity = []
    eq_index = []
    trades: List[Trade] = []

    position: Optional[Dict[str, float]] = None  # direction, entry, sl, tp
    entry_time: Optional[pd.Timestamp] = None

    for i in range(1, len(data)):
        window = data.iloc[: i + 1]
        bar = data.iloc[i]
        ts = window.index[-1]

        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])
        atr_v = float(bar.get("atr", np.nan))
        if not np.isfinite(atr_v) or atr_v <= 0:
            atr_v = max(abs(h - l), 1e-6)

        # Manage open position
        if position is not None and entry_time is not None:
            dirn = position["direction"]
            sl = position["sl"]
            tp = position["tp"]
            entry = position["entry"]

            exit_price = None
            # Conservative: assume SL hits first if both touched in same candle.
            if dirn == "BUY":
                if l <= sl:
                    exit_price = sl
                elif h >= tp:
                    exit_price = tp
            else:
                if h >= sl:
                    exit_price = sl
                elif l <= tp:
                    exit_price = tp

            if exit_price is not None:
                pnl = (exit_price - entry) if dirn == "BUY" else (entry - exit_price)
                balance += pnl
                trades.append(
                    Trade(
                        entry_time=entry_time,
                        exit_time=ts,
                        direction=dirn,
                        entry=entry,
                        exit=float(exit_price),
                        sl=sl,
                        tp=tp,
                        pnl=float(pnl),
                        win=pnl > 0,
                    )
                )
                position = None
                entry_time = None

        # Generate signal and potentially open a new position (single-position model)
        if position is None:
            sig = strategy.generate_signal(window)
            if sig in (Signal.BUY, Signal.SELL):
                direction = "BUY" if sig == Signal.BUY else "SELL"
                sl, tp = risk.calc_sl_tp(direction, c, atr_v)
                position = {"direction": direction, "entry": c, "sl": sl, "tp": tp}
                entry_time = ts

        equity.append(balance)
        eq_index.append(ts)

    equity_curve = pd.Series(equity, index=pd.Index(eq_index, name="time"), name="equity")
    total_pnl = float(equity_curve.iloc[-1] - initial_balance) if not equity_curve.empty else 0.0
    wins = sum(1 for t in trades if t.win)
    win_rate = float(wins / len(trades)) if trades else 0.0
    max_dd = _max_drawdown(equity_curve)
    
    # Calculate additional metrics
    profit_factor = 0.0
    avg_win = 0.0
    avg_loss = 0.0
    sharpe_ratio = 0.0
    
    if trades:
        winning_trades = [t.pnl for t in trades if t.win]
        losing_trades = [abs(t.pnl) for t in trades if not t.win]
        
        if winning_trades:
            avg_win = sum(winning_trades) / len(winning_trades)
        if losing_trades:
            avg_loss = sum(losing_trades) / len(losing_trades)
        
        total_wins = sum(winning_trades)
        total_losses = sum(losing_trades)
        
        if total_losses > 0:
            profit_factor = total_wins / total_losses
        else:
            profit_factor = float('inf') if total_wins > 0 else 0.0
        
        # Calculate Sharpe ratio (simplified)
        if len(equity_curve) > 1:
            returns = equity_curve.pct_change().dropna()
            if len(returns) > 1 and returns.std() > 0:
                sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(252))  # Annualized
    
    # Calculate trade frequency (trades per day)
    trade_frequency = 0.0
    if trades and len(data) > 0:
        time_span_days = (data.index[-1] - data.index[0]).total_seconds() / 86400
        if time_span_days > 0:
            trade_frequency = len(trades) / time_span_days

    return BacktestResult(
        trades=trades,
        total_pnl=total_pnl,
        win_rate=win_rate,
        max_drawdown=max_dd,
        equity_curve=equity_curve,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        sharpe_ratio=sharpe_ratio,
        trade_frequency=trade_frequency,
    )


def compare_strategies(
    df: pd.DataFrame,
    strategies: Dict[str, Tuple[BaseStrategy, RiskManager]],
    initial_balance: float = 10_000.0,
) -> Dict[str, BacktestResult]:
    """Compare multiple strategies on the same data."""
    results = {}
    for name, (strategy, risk) in strategies.items():
        logger.info(f"Backtesting strategy: {name}")
        result = run_backtest(df, strategy, risk, initial_balance)
        results[name] = result
    return results


def print_comparison(results: Dict[str, BacktestResult]) -> None:
    """Print a comparison table of backtest results."""
    print("\n" + "=" * 100)
    print("STRATEGY COMPARISON RESULTS")
    print("=" * 100)
    
    header = f"{'Strategy':<20} {'Trades':<8} {'Win Rate':<10} {'Total P&L':<12} {'Profit Factor':<14} {'Max DD':<10} {'Sharpe':<10} {'Freq/day':<10}"
    print(header)
    print("-" * 100)
    
    for name, result in results.items():
        trades_count = len(result.trades)
        win_rate_pct = f"{result.win_rate:.1%}"
        total_pnl_str = f"{result.total_pnl:.2f}"
        pf_str = f"{result.profit_factor:.2f}" if result.profit_factor != float('inf') else "∞"
        max_dd_str = f"{result.max_drawdown:.2f}"
        sharpe_str = f"{result.sharpe_ratio:.2f}"
        freq_str = f"{result.trade_frequency:.2f}"
        
        row = f"{name:<20} {trades_count:<8} {win_rate_pct:<10} {total_pnl_str:<12} {pf_str:<14} {max_dd_str:<10} {sharpe_str:<10} {freq_str:<10}"
        print(row)
    
    print("=" * 100)
    
    # Print detailed analysis for each strategy
    for name, result in results.items():
        print(f"\n{name} Detailed Analysis:")
        print(f"  Total Trades: {len(result.trades)}")
        print(f"  Win Rate: {result.win_rate:.1%}")
        print(f"  Average Win: ${result.avg_win:.2f}")
        print(f"  Average Loss: ${result.avg_loss:.2f}")
        print(f"  Profit Factor: {result.profit_factor:.2f}")
        print(f"  Max Drawdown: ${result.max_drawdown:.2f}")
        print(f"  Sharpe Ratio: {result.sharpe_ratio:.2f}")
        print(f"  Trade Frequency: {result.trade_frequency:.2f} trades/day")
        print(f"  Total P&L: ${result.total_pnl:.2f}")

