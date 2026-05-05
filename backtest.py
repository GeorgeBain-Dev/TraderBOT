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

    return BacktestResult(
        trades=trades,
        total_pnl=total_pnl,
        win_rate=win_rate,
        max_drawdown=max_dd,
        equity_curve=equity_curve,
    )

