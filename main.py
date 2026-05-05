from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd

try:
    from .config import BotConfig, validate_config
    from .data import MT5Client, MT5Error, get_latest_candles, get_live_price
    from .execution import ExecutionEngine
    from .optimizer import EdgeCriteria, calibrate_best_params
    from .risk import RiskManager
    from .strategy import RsiEmaStrategy, Signal
    from .notifier import ChangeDetector, WhatsAppConfig, WhatsAppNotifier, NotificationError
    from .utils import BotEvent, setup_logging, utc_now
except ImportError:
    # Fallback for direct execution
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    from config import BotConfig, validate_config
    from data import MT5Client, MT5Error, get_latest_candles, get_live_price
    from execution import ExecutionEngine
    from optimizer import EdgeCriteria, calibrate_best_params
    from risk import RiskManager
    from strategy import RsiEmaStrategy, Signal
    from notifier import ChangeDetector, WhatsAppConfig, WhatsAppNotifier, NotificationError
    from utils import BotEvent, setup_logging, utc_now

logger = setup_logging()


@dataclass
class BotStatus:
    running: bool = False
    mode: str = "IDLE"  # CALIBRATING | NO_EDGE | LIVE | IDLE
    last_signal: str = "HOLD"
    symbol: str = ""
    timeframe: str = ""
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    balance: float = 0.0
    equity: float = 0.0
    free_margin: float = 0.0
    positions_count: int = 0
    positions_summary: str = ""
    last_update_utc: Optional[str] = None
    last_calibration_utc: Optional[str] = None
    last_error: Optional[str] = None


class TradingBot:
    def __init__(
        self,
        cfg: BotConfig,
        event_q: "queue.Queue[BotEvent]",
        client: Optional[MT5Client] = None,
        wa_notifier: Optional[WhatsAppNotifier] = None,
    ):
        validate_config(cfg)
        self.cfg = cfg
        self.event_q = event_q
        self.status = BotStatus()

        self.client = client or MT5Client()
        self.strategy = RsiEmaStrategy(
            use_ml_confirmation=cfg.use_ml_confirmation,
            ml_min_train_rows=cfg.ml_min_train_rows,
        )
        # Initialize with timeframe-specific parameters
        self.strategy.update_timeframe(cfg.timeframe)
        self.risk = RiskManager(
            risk_per_trade=cfg.risk_per_trade,
            sl_atr_mult=cfg.sl_atr_mult,
            tp_rr=cfg.tp_rr,
        )
        self.exec = ExecutionEngine(
            client=self.client,
            deviation_points=cfg.deviation_points,
            magic_number=cfg.magic_number,
            comment=cfg.comment,
            allow_live_trading=cfg.allow_live_trading,
        )

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._next_calibration_ts = 0.0
        self._account_logged = False
        self._wa = wa_notifier
        self._changes = ChangeDetector()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="bot-loop", daemon=True)
        self.status.running = True
        self.status.mode = "CALIBRATING"  # Always calibrate on start for market analysis
        self._emit("INFO", "Bot started - performing initial market analysis...")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.status.running = False
        self.status.mode = "IDLE"
        self._emit("INFO", "Bot stopping...")

    def _emit(self, level: str, message: str) -> None:
        evt = BotEvent(ts=utc_now(), level=level, message=message)
        # Persist events to file/console logs too (not only the UI queue)
        lvl = level.upper()
        if lvl == "DEBUG":
            logger.debug(message)
        elif lvl == "INFO":
            logger.info(message)
        elif lvl == "WARN" or lvl == "WARNING":
            logger.warning(message)
        elif lvl == "ERROR":
            logger.error(message)
        else:
            logger.info("[%s] %s", level, message)
        try:
            self.event_q.put_nowait(evt)
        except Exception:
            pass

    def _notify_changes(self) -> None:
        if self._wa is None:
            return
        msg = self._changes.format_change_message(
            symbol=self.cfg.symbol,
            mode=self.status.mode,
            signal=self.status.last_signal,
            price=float(self.status.last_price or 0.0),
            positions_count=int(self.status.positions_count or 0),
        )
        if not msg:
            return
        try:
            self._wa.send(msg)
        except NotificationError as e:
            # Don't spam; log once via event stream.
            self._emit("WARN", f"WhatsApp not configured: {e}")
            self._wa.cfg.enabled = False
        except Exception as e:
            self._emit("WARN", f"WhatsApp send failed: {type(e).__name__}: {e}")

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                # Update timeframe if changed
                if self.status.timeframe != self.cfg.timeframe:
                    self.status.timeframe = self.cfg.timeframe
                    # Update strategy parameters for new timeframe
                    if hasattr(self.strategy, 'update_timeframe'):
                        self.strategy.update_timeframe(self.cfg.timeframe)
                        self._emit("INFO", f"Strategy updated for timeframe {self.cfg.timeframe}")
                
                self.status.symbol = self.cfg.symbol

                # Account & positions (safe best-effort; do not block strategy loop on failures)
                try:
                    account = self.client.account_info()
                    self.status.balance = float(getattr(account, "balance", 0.0) or 0.0)
                    self.status.equity = float(getattr(account, "equity", 0.0) or 0.0)
                    self.status.free_margin = float(getattr(account, "margin_free", 0.0) or 0.0)
                    if not self._account_logged:
                        login = getattr(account, "login", None)
                        name = getattr(account, "name", None)
                        server = getattr(account, "server", None)
                        currency = getattr(account, "currency", None)
                        self._emit(
                            "INFO",
                            f"Connected account: login={login} name={name} server={server} currency={currency}",
                        )
                        self._account_logged = True
                except Exception as e:
                    logger.debug("Account info fetch failed: %s", e)

                try:
                    pos = self.exec.positions()
                    self.status.positions_count = len(pos)
                    self.status.positions_summary = self._format_positions(pos)
                except Exception as e:
                    logger.debug("Positions fetch failed: %s", e)

                bid, ask, ts = get_live_price(self.client, self.cfg.symbol)
                self.status.bid = float(bid)
                self.status.ask = float(ask)
                mid = (bid + ask) / 2.0 if bid and ask else (bid or ask or 0.0)
                self.status.mid = float(mid)
                self.status.last_update_utc = ts.isoformat()

                # Initial calibration on start (always runs once)
                if self.status.mode == "CALIBRATING" and not hasattr(self, '_initial_calibrated'):
                    self._emit("INFO", "Performing initial market analysis...")
                    hist = get_latest_candles(
                        self.client,
                        self.cfg.symbol,
                        self.cfg.timeframe,
                        n=self.cfg.calibration_candles,
                    )
                    crit = EdgeCriteria(
                        min_trades=self.cfg.min_trades_for_edge,
                        min_profit_factor=self.cfg.min_profit_factor,
                        min_total_pnl=self.cfg.min_total_pnl,
                        max_drawdown_abs=self.cfg.max_drawdown_abs,
                        min_win_rate=0.70,  # Target 70%+ win rate for more activity
                    )
                    best = calibrate_best_params(hist, criteria=crit)
                    self.status.last_calibration_utc = utc_now().isoformat()
                    self._initial_calibrated = True

                    if best is None:
                        self.status.mode = "LIVE"  # Still trade with default parameters
                        self._emit("WARN", "No edge found - using default parameters for live trading")
                    else:
                        # Apply calibrated parameters
                        self.strategy = RsiEmaStrategy(
                            use_ml_confirmation=self.cfg.use_ml_confirmation,
                            ml_min_train_rows=self.cfg.ml_min_train_rows,
                        )
                        # Update with timeframe-specific parameters
                        self.strategy.update_timeframe(self.cfg.timeframe)
                        self._emit("INFO", f"Applied calibrated parameters for {self.cfg.timeframe}")
                        self.risk = RiskManager(
                            risk_per_trade=self.cfg.risk_per_trade,
                            sl_atr_mult=best.sl_atr_mult,
                            tp_rr=best.tp_rr,
                        )
                        self.status.mode = "LIVE"
                        self._emit(
                            "INFO",
                            f"ANALYSIS COMPLETE: wr={best.win_rate:.1%} pf={best.profit_factor:.2f} trades={best.trades} "
                            f"pnl={best.total_pnl:.5f} dd={best.max_drawdown:.5f} "
                            f"(RSI={best.rsi_period}, EMA={best.ema_period}, SLxATR={best.sl_atr_mult}, RR={best.tp_rr})",
                        )

                # Auto-calibration: periodic re-analysis if enabled
                if self.cfg.auto_calibrate and time.time() >= self._next_calibration_ts:
                    # Don't change mode to CALIBRATING if continuous calibrator is running
                    if not hasattr(self, '_continuous_calibrator') or not self._continuous_calibrator:
                        self.status.mode = "CALIBRATING"
                        self._emit("INFO", "Calibrating on recent history (edge detection)...")
                    hist = get_latest_candles(
                        self.client,
                        self.cfg.symbol,
                        self.cfg.timeframe,
                        n=self.cfg.calibration_candles,
                    )
                    crit = EdgeCriteria(
                        min_trades=self.cfg.min_trades_for_edge,
                        min_profit_factor=self.cfg.min_profit_factor,
                        min_total_pnl=self.cfg.min_total_pnl,
                        max_drawdown_abs=self.cfg.max_drawdown_abs,
                        min_win_rate=0.70,  # Target 70%+ win rate for more activity
                    )
                    best = calibrate_best_params(hist, criteria=crit)
                    self.status.last_calibration_utc = utc_now().isoformat()
                    self._next_calibration_ts = time.time() + float(self.cfg.calibration_every_minutes) * 60.0

                    if best is None:
                        self.status.mode = "NO_EDGE"
                        self._emit("WARN", "NO_EDGE: no profitable configuration found; will keep watching.")
                    else:
                        # Apply calibrated parameters
                        self.strategy = RsiEmaStrategy(
                            use_ml_confirmation=self.cfg.use_ml_confirmation,
                            ml_min_train_rows=self.cfg.ml_min_train_rows,
                        )
                        # Update with timeframe-specific parameters
                        self.strategy.update_timeframe(self.cfg.timeframe)
                        self._emit("INFO", f"Applied calibrated parameters for {self.cfg.timeframe}")
                        self.risk = RiskManager(
                            risk_per_trade=self.cfg.risk_per_trade,
                            sl_atr_mult=best.sl_atr_mult,
                            tp_rr=best.tp_rr,
                        )
                        self.status.mode = "LIVE"
                        self._emit(
                            "INFO",
                            f"LIVE: edge found wr={best.win_rate:.1%} pf={best.profit_factor:.2f} trades={best.trades} "
                            f"pnl={best.total_pnl:.5f} dd={best.max_drawdown:.5f} "
                            f"(RSI={best.rsi_period}, EMA={best.ema_period}, SLxATR={best.sl_atr_mult}, RR={best.tp_rr})",
                        )

                df = get_latest_candles(
                    self.client, self.cfg.symbol, self.cfg.timeframe, n=self.cfg.candles
                )
                if df.empty:
                    raise MT5Error("No candles received (empty dataframe).")

                # Use continuously calibrated strategy if available
                if hasattr(self, '_continuous_calibrator') and self._continuous_calibrator:
                    strategy = self._continuous_calibrator.get_current_strategy()
                    risk = self._continuous_calibrator.get_current_risk()
                else:
                    strategy = self.strategy
                    risk = self.risk

                df_prep = strategy.prepare(df)
                sig = strategy.generate_signal(df_prep)
                self.status.last_signal = sig.value
                self._notify_changes()

                can_trade = True  # Always trade when signals are generated
                if (
                    can_trade
                    and sig in (Signal.BUY, Signal.SELL)
                    and not self.exec.has_position(self.cfg.symbol)
                ):
                    balance = float(self.status.balance or self.status.equity or 0.0)
                    atr_v = float(df_prep.iloc[-1].get("atr", 0.0))
                    direction = sig.value
                    entry = ask if direction == "BUY" else bid
                    
                    # Dynamic position sizing for small and big trades
                    plan = risk.plan_trade(
                        direction=direction,
                        entry=float(entry),
                        atr_value=float(atr_v),
                        balance=float(balance),
                        client=self.client,
                        symbol=self.cfg.symbol,
                    )
                    
                    if plan.volume <= 0:
                        self._emit("WARN", "Signal but volume computed as 0; skipping order.")
                    else:
                        # Calculate potential profit to determine if it's a big trade
                        potential_profit = abs(plan.tp - plan.entry) * plan.volume
                        is_big_trade = potential_profit > (balance * 0.02)  # More than 2% of balance = big trade
                        
                        res = self.exec.place_market_order(
                            symbol=self.cfg.symbol,
                            direction=plan.direction,
                            volume=plan.volume,
                            sl=plan.sl,
                            tp=plan.tp,
                        )
                        
                        # Add trade to monitor if successful
                        if res.ok and res.order_id:
                            try:
                                # Get the trade monitor from the UI if available
                                if hasattr(self, '_trade_monitor') and self._trade_monitor:
                                    self._trade_monitor.add_trade(
                                        order_id=res.order_id,
                                        symbol=self.cfg.symbol,
                                        type=plan.direction,
                                        volume=plan.volume,
                                        entry_price=float(entry),
                                        stop_loss=plan.sl,
                                        take_profit=plan.tp
                                    )
                            except Exception as e:
                                logger.error(f"Failed to add trade to monitor: {e}")
                        
                        trade_type = "BIG TRADE" if is_big_trade else "SMALL PROFIT TRADE"
                        self._emit("INFO" if res.ok else "ERROR", f"{trade_type}: {res.message}")
                        self._notify_changes()
                else:
                    spread = (self.status.ask - self.status.bid) if (self.status.ask and self.status.bid) else 0.0
                    self._emit(
                        "INFO",
                        f"Status: {self.status.symbol} {self.status.timeframe} | {self.status.mode} | {sig.value} | "
                        f"bid={self.status.bid:.5f} ask={self.status.ask:.5f} mid={self.status.mid:.5f} spr={spread:.5f}",
                    )

                self.status.last_error = None
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.status.last_error = msg
                logger.exception("Bot loop error")
                self._emit("ERROR", msg)

            time.sleep(self.cfg.poll_seconds)

        try:
            self.client.shutdown()
        except Exception:
            pass
        self._emit("INFO", "Bot stopped.")

    @staticmethod
    def _format_positions(positions: object, max_items: int = 3) -> str:
        try:
            pos_list = list(positions)  # type: ignore[arg-type]
        except Exception:
            return ""
        if not pos_list:
            return ""

        parts = []
        for p in pos_list[:max_items]:
            sym = str(getattr(p, "symbol", ""))
            vol = float(getattr(p, "volume", 0.0) or 0.0)
            ptype = int(getattr(p, "type", -1))
            side = "BUY" if ptype == 0 else ("SELL" if ptype == 1 else str(ptype))
            profit = float(getattr(p, "profit", 0.0) or 0.0)
            parts.append(f"{sym} {side} {vol:.2f} pnl={profit:.2f}")

        more = "" if len(pos_list) <= max_items else f" (+{len(pos_list) - max_items} more)"
        return " | ".join(parts) + more


def main() -> None:
    try:
        from .ui import run_app
    except ImportError:
        from ui import run_app
    
    # Generate unique run ID for this session
    try:
        from .utils import utc_now
    except ImportError:
        from utils import utc_now
    run_id = utc_now().strftime('%Y%m%d_%H%M%S')
    
    # Setup logging with unique run ID
    try:
        from .utils import setup_logging
    except ImportError:
        from utils import setup_logging
    logger = setup_logging(run_id=run_id)
    logger.info("Starting new bot session")
    
    run_app()


if __name__ == "__main__":
    main()
