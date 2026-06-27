from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

try:
    from .config import BotConfig
    from .data import MT5Client, get_latest_candles
    from .optimizer import CalibratedParams, EdgeCriteria, calibrate_best_params
    from .risk import RiskManager
    from .strategy import RsiEmaStrategy
    from .utils import setup_logging
except ImportError:
    from config import BotConfig
    from data import MT5Client, get_latest_candles
    from optimizer import CalibratedParams, EdgeCriteria, calibrate_best_params
    from risk import RiskManager
    from strategy import RsiEmaStrategy
    from utils import setup_logging

logger = setup_logging()


@dataclass
class CalibrationResult:
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
    """Background re-calibration using the shared optimizer grid."""

    def __init__(self, client: MT5Client, config: BotConfig):
        self.client = client
        self.config = config
        self.is_running = False
        self.calibration_thread: Optional[threading.Thread] = None
        self.calibration_history: List[CalibrationResult] = []
        self.current_params: Optional[CalibratedParams] = None
        self.last_calibration_time: Optional[datetime] = None

        self.strategy = RsiEmaStrategy(
            use_ml_confirmation=config.use_ml_confirmation,
            ml_min_train_rows=config.ml_min_train_rows,
        )
        self.strategy.apply_config(config)
        self.strategy.update_timeframe(config.timeframe)
        self.risk = RiskManager(
            risk_per_trade=config.risk_per_trade,
            sl_atr_mult=config.sl_atr_mult,
            tp_rr=config.tp_rr,
        )

    def update_config(self, config: BotConfig) -> None:
        self.config = config
        self.strategy.apply_config(config)
        self.strategy.update_timeframe(config.timeframe)
        self.risk = RiskManager(
            risk_per_trade=config.risk_per_trade,
            sl_atr_mult=config.sl_atr_mult,
            tp_rr=config.tp_rr,
        )

    def start_continuous_calibration(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self.calibration_thread = threading.Thread(
            target=self._calibration_loop, name="continuous-cal", daemon=True
        )
        self.calibration_thread.start()
        logger.info("Continuous calibration started")

    def stop_continuous_calibration(self) -> None:
        self.is_running = False
        if self.calibration_thread:
            self.calibration_thread.join(timeout=10)
        logger.info("Continuous calibration stopped")

    def _edge_criteria(self) -> EdgeCriteria:
        c = self.config
        return EdgeCriteria(
            min_trades=c.min_trades_for_edge,
            min_profit_factor=c.min_profit_factor,
            min_total_pnl=c.min_total_pnl,
            max_drawdown_abs=c.max_drawdown_abs,
            min_win_rate=c.min_win_rate,
        )

    def _calibration_loop(self) -> None:
        interval = max(1, self.config.continuous_calibration_interval_minutes) * 60
        while self.is_running:
            try:
                result = self._perform_calibration()
                self.calibration_history.append(result)
                if result.edge_found and result.params:
                    self._apply_params(result.params)
                cutoff = datetime.now(timezone.utc).timestamp() - 86400
                self.calibration_history = [
                    r for r in self.calibration_history if r.timestamp.timestamp() > cutoff
                ]
            except Exception as e:
                logger.error("Calibration loop error: %s", e)
            time.sleep(interval)

    def _perform_calibration(self) -> CalibrationResult:
        start = time.time()
        cfg = self.config
        try:
            df = get_latest_candles(
                self.client,
                cfg.symbol,
                cfg.timeframe,
                n=min(cfg.calibration_candles, 2500),
            )
            if df.empty or len(df) < 400:
                return self._empty_result(start)

            best = calibrate_best_params(
                df, criteria=self._edge_criteria(), initial_balance=10_000.0
            )
            if best is None:
                return CalibrationResult(
                    timestamp=datetime.now(timezone.utc),
                    params=None,
                    edge_found=False,
                    profit_factor=0.0,
                    trades_count=0,
                    total_pnl=0.0,
                    win_rate=0.0,
                    max_drawdown=0.0,
                    execution_time=time.time() - start,
                )

            self.last_calibration_time = datetime.now(timezone.utc)
            return CalibrationResult(
                timestamp=self.last_calibration_time,
                params=best,
                edge_found=True,
                profit_factor=best.profit_factor,
                trades_count=best.trades,
                total_pnl=best.total_pnl,
                win_rate=best.win_rate,
                max_drawdown=best.max_drawdown,
                execution_time=time.time() - start,
            )
        except Exception as e:
            logger.error("Calibration failed: %s", e)
            return self._empty_result(start)

    def _empty_result(self, start: float) -> CalibrationResult:
        return CalibrationResult(
            timestamp=datetime.now(timezone.utc),
            params=None,
            edge_found=False,
            profit_factor=0.0,
            trades_count=0,
            total_pnl=0.0,
            win_rate=0.0,
            max_drawdown=0.0,
            execution_time=time.time() - start,
        )

    def _apply_params(self, params: CalibratedParams) -> None:
        self.current_params = params
        self.strategy.rsi_period = params.rsi_period
        self.strategy.ema_period = params.ema_period
        self.risk.sl_atr_mult = params.sl_atr_mult
        self.risk.tp_rr = params.tp_rr
        logger.info(
            "Calibrator applied RSI=%s EMA=%s SLxATR=%s RR=%s",
            params.rsi_period,
            params.ema_period,
            params.sl_atr_mult,
            params.tp_rr,
        )

    def get_current_strategy(self) -> RsiEmaStrategy:
        return self.strategy

    def get_current_risk(self) -> RiskManager:
        return self.risk

    def get_calibration_status(self) -> Dict:
        return {
            "is_running": self.is_running,
            "last_calibration": (
                self.last_calibration_time.isoformat() if self.last_calibration_time else None
            ),
            "current_params": (
                self.current_params.__dict__ if self.current_params else None
            ),
            "calibration_count_24h": len(self.calibration_history),
            "recent_edges": sum(1 for r in self.calibration_history if r.edge_found),
        }
