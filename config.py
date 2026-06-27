from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, List, Optional, Tuple


SUPPORTED_TIMEFRAMES: Tuple[str, ...] = (
    "M1", "M5", "M15", "M30", "H1", "H4", "D1",
)


@dataclass
class BotConfig:
    """Single source of truth for bot parameters (UI reads/writes via asdict)."""

    # Core
    symbol: str = ""
    timeframe: str = "M5"
    candles: int = 300
    poll_seconds: float = 2.0
    test_mode: bool = False

    # Auto-calibration
    auto_calibrate: bool = True
    continuous_calibrate: bool = True
    calibration_candles: int = 2500
    calibration_every_minutes: int = 15
    continuous_calibration_interval_minutes: int = 5
    min_trades_for_edge: int = 2
    min_profit_factor: float = 1.005
    min_total_pnl: float = -0.01
    max_drawdown_abs: float = 0.15
    min_win_rate: float = 0.40

    # Strategy (used when not overridden by timeframe presets)
    rsi_period: int = 14
    ema_period: int = 100
    rsi_buy: float = 30.0
    rsi_sell: float = 70.0
    signal_strength_threshold: float = 2.5  # Increased to stop losing trades
    use_multi_timeframe_confirmation: bool = True  # Re-enabled for quality

    # Risk / execution
    risk_per_trade: float = 0.005  # Reduced from 1% to 0.5% to limit position size
    sl_atr_mult: float = 1.0  # Reduced from 1.5 to 1.0 to limit losses
    tp_rr: float = 2.0
    deviation_points: int = 20
    magic_number: int = 26052026
    comment: str = "pybot-rsi-ema"
    allow_live_trading: bool = True

    # ML
    use_ml_confirmation: bool = False
    ml_min_train_rows: int = 500

    # Trade management: trail (default) | broker_only | giveback | full
    trade_management_mode: str = "trail"
    monitor_poll_seconds: float = 2.0
    monitor_timeframe: str = ""  # empty = use main timeframe
    monitor_candles: int = 100
    min_trade_duration_minutes: int = 1
    max_trade_duration_minutes: int = 240
    monitor_grace_seconds: int = 60  # no discretionary close right after entry
    reentry_cooldown_seconds: int = 60  # Reduced from 120s to allow faster re-entry

    # Trail mode fallbacks (per-timeframe presets override when timeframe is set)
    trail_activate_risk_multiple: float = 0.10  # Lowered to arm trail sooner
    trail_atr_mult: float = 1.1
    breakeven_risk_multiple: float = 0.25  # 0 = disable break-even step

    # Profit giveback (giveback / full modes only)
    min_peak_profit_to_protect: float = 0.0  # 0 = use % of risk (see below)
    min_peak_profit_risk_fraction: float = 0.50  # arm giveback after 50% of risk in profit
    profit_giveback_ratio: float = 0.60  # close if profit < 60% of peak (allow winners to run)
    profit_giveback_min_drop: float = 0.0  # 0 = use fraction of risk
    profit_giveback_min_drop_risk_fraction: float = 0.15
    lock_profit_after_peak: float = 0.0  # 0 = off; else close if profit falls below this after peak exceeded it

    # Profit protection thresholds
    min_profit_percent_for_protection: float = 0.50
    min_profit_percent_fallback: float = 0.25  # fallback threshold for small profit taking (25% of risk)
    profit_fade_ratio: float = 0.60
    trailing_stop_percent: float = 0.5
    max_profit_target_percent: float = 2.0
    max_loss_percent: float = 1.5

    # Entry controls
    max_entry_volatility_percent: float = 5.0  # skip entries when volatility (%) exceeds this

    # Loss protection
    loss_risk_close_ratio: float = 0.50  # Reduced from 0.75 to 0.50
    loss_timeout_minutes: int = 45
    max_loss_per_trade: float = 0.002  # Hard cap: close if loss exceeds 0.2% of account

    # UI / symbols
    max_symbols_in_dropdown: int = 200


@dataclass(frozen=True)
class TimeframeTrailParams:
    """Trailing-stop behaviour tuned per chart timeframe."""

    trail_activate_risk_multiple: float
    trail_atr_mult: float
    monitor_grace_seconds: int
    breakeven_risk_multiple: float


# Per-TF presets (ATR trail distance scales with timeframe volatility)
TIMEFRAME_TRAIL_PRESETS: Dict[str, TimeframeTrailParams] = {
    "M1": TimeframeTrailParams(0.10, 1.0, 30, 0.20),
    "M5": TimeframeTrailParams(0.10, 1.1, 60, 0.25),
    "M15": TimeframeTrailParams(0.20, 1.3, 75, 0.30),
    "M30": TimeframeTrailParams(0.35, 1.7, 90, 0.45),
    "H1": TimeframeTrailParams(0.50, 2.2, 120, 0.50),
    "H4": TimeframeTrailParams(0.60, 2.8, 180, 0.55),
    "D1": TimeframeTrailParams(0.70, 3.2, 300, 0.60),
}


def get_trail_params(timeframe: str, cfg: BotConfig) -> TimeframeTrailParams:
    """Resolve trail settings for the active timeframe (preset or config fallback)."""
    preset = TIMEFRAME_TRAIL_PRESETS.get(timeframe.upper())
    if preset is not None:
        return preset
    return TimeframeTrailParams(
        trail_activate_risk_multiple=cfg.trail_activate_risk_multiple,
        trail_atr_mult=cfg.trail_atr_mult,
        monitor_grace_seconds=cfg.monitor_grace_seconds,
        breakeven_risk_multiple=cfg.breakeven_risk_multiple,
    )


def validate_config(cfg: BotConfig) -> None:
    if not cfg.symbol.strip():
        raise ValueError("symbol must be set (select a symbol in the UI or config).")
    if cfg.timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {SUPPORTED_TIMEFRAMES}.")
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
    if not (0 < cfg.profit_giveback_ratio < 1):
        raise ValueError("profit_giveback_ratio must be in (0, 1).")
    if cfg.monitor_grace_seconds < 0:
        raise ValueError("monitor_grace_seconds must be >= 0.")
    if cfg.reentry_cooldown_seconds < 0:
        raise ValueError("reentry_cooldown_seconds must be >= 0.")
    allowed_modes = ("broker_only", "trail", "giveback", "full")
    if cfg.trade_management_mode not in allowed_modes:
        raise ValueError(f"trade_management_mode must be one of {allowed_modes}.")


def config_field_specs() -> List[Tuple[str, str, type]]:
    """(field_name, human label, python type) for UI generation."""
    labels = {
        "symbol": "Symbol",
        "timeframe": "Timeframe",
        "candles": "Candles (history bars)",
        "poll_seconds": "Bot poll interval (sec)",
        "test_mode": "Test mode",
        "auto_calibrate": "Auto-calibrate on schedule",
        "continuous_calibrate": "Continuous background calibration",
        "calibration_candles": "Calibration candle count",
        "calibration_every_minutes": "Calibration interval (min)",
        "continuous_calibration_interval_minutes": "Continuous cal. interval (min)",
        "min_trades_for_edge": "Min trades for edge",
        "min_profit_factor": "Min profit factor",
        "min_total_pnl": "Min total PnL (backtest)",
        "max_drawdown_abs": "Max drawdown (abs)",
        "min_win_rate": "Min win rate",
        "rsi_period": "RSI period",
        "ema_period": "EMA period",
        "rsi_buy": "RSI buy level",
        "rsi_sell": "RSI sell level",
        "signal_strength_threshold": "Signal strength threshold",
        "use_multi_timeframe_confirmation": "Multi-timeframe confirmation",
        "risk_per_trade": "Risk per trade (fraction)",
        "sl_atr_mult": "Stop loss (ATR multiple)",
        "tp_rr": "Take profit R:R",
        "deviation_points": "Order deviation (points)",
        "magic_number": "Magic number",
        "comment": "Order comment",
        "allow_live_trading": "Allow live orders",
        "use_ml_confirmation": "ML confirmation",
        "ml_min_train_rows": "ML min training rows",
        "trade_management_mode": "Trade management (broker_only/trail/giveback/full)",
        "trail_activate_risk_multiple": "Trail fallback: activate after N × risk",
        "trail_atr_mult": "Trail fallback: ATR distance multiplier",
        "breakeven_risk_multiple": "Trail fallback: break-even at N × risk (0=off)",
        "monitor_poll_seconds": "Monitor poll (sec)",
        "min_peak_profit_risk_fraction": "Giveback: arm after N × risk profit",
        "profit_giveback_min_drop_risk_fraction": "Giveback: min drop as N × risk",
        "monitor_timeframe": "Monitor timeframe (blank=main)",
        "monitor_candles": "Monitor candles",
        "min_trade_duration_minutes": "Min trade duration (min)",
        "max_trade_duration_minutes": "Max trade duration (min)",
        "monitor_grace_seconds": "Grace period after entry (sec)",
        "reentry_cooldown_seconds": "Cooldown before re-entry (sec)",
        "min_peak_profit_to_protect": "Min peak profit to arm giveback",
        "profit_giveback_ratio": "Profit giveback ratio (0-1)",
        "profit_giveback_min_drop": "Min profit drop to trigger giveback",
        "lock_profit_after_peak": "Lock profit floor after peak",
        "min_profit_percent_for_protection": "Min profit % for protection",
        "profit_fade_ratio": "Profit fade ratio vs peak",
        "trailing_stop_percent": "Trailing stop %",
        "max_profit_target_percent": "Max profit target %",
        "max_loss_percent": "Max loss %",
        "loss_risk_close_ratio": "Close at % of initial risk",
        "max_entry_volatility_percent": "Max entry volatility %",
        "loss_timeout_minutes": "Loss timeout (min)",
        "max_symbols_in_dropdown": "Max symbols in dropdown",
    }
    specs: List[Tuple[str, str, type]] = []
    for f in fields(BotConfig):
        t = f.type if isinstance(f.type, type) else str
        if f.name in labels:
            specs.append((f.name, labels[f.name], bool if f.type is bool or f.type == bool else t))
        else:
            specs.append((f.name, f.name.replace("_", " ").title(), bool if f.type is bool else t))
    return specs


def config_from_dict(data: Dict[str, Any], base: BotConfig | None = None) -> BotConfig:
    base = base or BotConfig()
    merged = asdict(base)
    valid_names = {f.name for f in fields(BotConfig)}
    for key, value in data.items():
        if key not in valid_names:
            continue
        field_type = type(getattr(base, key))
        if field_type is bool:
            merged[key] = bool(value) if not isinstance(value, bool) else value
        elif field_type is int:
            merged[key] = int(float(value))
        elif field_type is float:
            merged[key] = float(value)
        elif field_type is str:
            merged[key] = str(value).strip()
        else:
            merged[key] = value
    return BotConfig(**merged)
