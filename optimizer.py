from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from .backtest import run_backtest
except ImportError:
    from backtest import run_backtest
try:
    from .risk import RiskManager
except ImportError:
    from risk import RiskManager
try:
    from .strategy import RsiEmaStrategy
except ImportError:
    from strategy import RsiEmaStrategy
try:
    from .utils import setup_logging
except ImportError:
    from utils import setup_logging

logger = setup_logging()


@dataclass(frozen=True)
class EdgeCriteria:
    min_trades: int = 3  # Minimum 3 trades for statistical significance
    min_profit_factor: float = 1.01  # Allow very small profits (1%+)
    min_total_pnl: float = 0.0  # Allow any positive PnL
    max_drawdown_abs: float = 0.10  # Higher tolerance for small profits
    min_win_rate: float = 0.70  # Still target 70%+ win rate


@dataclass(frozen=True)
class CalibratedParams:
    rsi_period: int
    ema_period: int
    sl_atr_mult: float
    tp_rr: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    trades: int
    win_rate: float


def _profit_factor(trades_pnl: Iterable[float]) -> float:
    gross_profit = 0.0
    gross_loss = 0.0
    for p in trades_pnl:
        if p > 0:
            gross_profit += p
        elif p < 0:
            gross_loss += -p
    if gross_loss <= 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calibrate_best_params(
    df: pd.DataFrame,
    *,
    criteria: EdgeCriteria,
    initial_balance: float = 10_000.0,
) -> Optional[CalibratedParams]:
    """Search a small grid and return best params if edge criteria satisfied.

    This is intentionally conservative and fast enough to run in a live bot loop.
    """
    if df is None or df.empty or len(df) < 400:
        return None

    # Optimized grid for faster calibration (16 combinations total)
    rsi_periods = [14, 21]  # Reduced to 2 options
    ema_periods = [100, 200]  # Reduced to 2 options  
    sl_mults = [1.5, 2.0]  # Reduced to 2 options
    rr_values = [2.0, 2.5]  # Reduced to 2 options

    best: Optional[CalibratedParams] = None
    best_score = -float("inf")

    for rp in rsi_periods:
        for ep in ema_periods:
            strat = RsiEmaStrategy()
            strat.update_timeframe("M5")  # Use default timeframe for calibration
            for slm in sl_mults:
                for rr in rr_values:
                    risk = RiskManager(risk_per_trade=0.01, sl_atr_mult=slm, tp_rr=rr)
                    res = run_backtest(df, strat, risk, initial_balance=initial_balance)
                    trades_n = len(res.trades)
                    if trades_n < criteria.min_trades:
                        continue

                    pf = _profit_factor([t.pnl for t in res.trades])
                    if not np.isfinite(pf) or pf < criteria.min_profit_factor:
                        continue
                    if res.total_pnl < criteria.min_total_pnl:
                        continue
                    if abs(res.max_drawdown) > criteria.max_drawdown_abs:
                        continue
                    if res.win_rate < criteria.min_win_rate:
                        continue

                    # Score heavily favors trade frequency and win rate, allows small profits
                    score = (res.win_rate * 5.0) + np.log1p(trades_n) * 2.0 + float(res.total_pnl) * 10.0
                    if score > best_score:
                        best_score = score
                        best = CalibratedParams(
                            rsi_period=rp,
                            ema_period=ep,
                            sl_atr_mult=slm,
                            tp_rr=rr,
                            profit_factor=float(pf),
                            total_pnl=float(res.total_pnl),
                            max_drawdown=float(res.max_drawdown),
                            trades=trades_n,
                            win_rate=float(res.win_rate),
                        )

    return best
