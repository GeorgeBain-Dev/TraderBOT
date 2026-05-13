from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BotConfig:
    # Core
    symbol: str = "XRPUSD"  # Default to XRPUSD for more volatility
    timeframe: str = "M5"  # MT5 timeframe string, mapped in data.py
    candles: int = 300
    poll_seconds: float = 2.0

    # Live testing helper (more frequent trades)
    test_mode: bool = False

    # Auto-calibration ("edge detection")
    # Default False so the bot behaves like a typical live EA:
    # it trades the strategy signals directly. Turn this on if you want
    # the optimizer to gate trading (LIVE/NO_EDGE).
    auto_calibrate: bool = True  # Disable to allow immediate trading
    calibration_candles: int = 2500
    calibration_every_minutes: int = 15
    min_trades_for_edge: int = 2  # Lower minimum for more flexibility
    min_profit_factor: float = 1.005  # Even lower threshold (0.5%+)
    min_total_pnl: float = -0.01  # Allow small negative PnL for learning
    max_drawdown_abs: float = 0.15  # Higher tolerance for small profits

    # Strategy params
    rsi_period: int = 14
    ema_period: int = 100  # Reduced from 200 for faster signals
    rsi_buy: float = 25.0  # More aggressive for XRPUSD volatility
    rsi_sell: float = 75.0  # More aggressive for XRPUSD volatility

    # Risk / execution
    risk_per_trade: float = 0.01  # 1%
    sl_atr_mult: float = 1.5  # used with ATR for SL distance (backtest/live)
    tp_rr: float = 2.0  # take-profit at RR*SL distance
    deviation_points: int = 20
    magic_number: int = 26052026
    comment: str = "pybot-rsi-ema"

    # Optional ML confirmation
    use_ml_confirmation: bool = False
    ml_min_train_rows: int = 500

    # Safety
    allow_live_trading: bool = True  # set False to disable order placement


def validate_config(cfg: BotConfig) -> None:
    if not (0 < cfg.risk_per_trade <= 0.05):
        raise ValueError("risk_per_trade must be in (0, 0.05].")
    if cfg.candles < 100:
        raise ValueError("candles must be >= 100.")
    if cfg.poll_seconds < 0.5:
        raise ValueError("poll_seconds must be >= 0.5.")
    if cfg.calibration_candles < 300:
        raise ValueError("calibration_candles must be >= 300.")
    if cfg.calibration_every_minutes < 1:
        raise ValueError("calibration_every_minutes must be >= 1.")

