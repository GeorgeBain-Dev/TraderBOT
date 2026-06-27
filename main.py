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
    from .risk import RiskManager, money_at_risk
    from .strategy import RsiEmaStrategy, Signal
    from .notifier import ChangeDetector, WhatsAppConfig, WhatsAppNotifier, NotificationError
    from .signal_tracker import SignalTracker
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
    from risk import RiskManager, money_at_risk
    from strategy import RsiEmaStrategy, Signal
    from notifier import ChangeDetector, WhatsAppConfig, WhatsAppNotifier, NotificationError
    from signal_tracker import SignalTracker
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
        self._signal_tracker = SignalTracker()
        self._last_position_profit = 0.0
        
        self.strategy = RsiEmaStrategy(
            use_ml_confirmation=cfg.use_ml_confirmation,
            ml_min_train_rows=cfg.ml_min_train_rows,
            signal_strength_threshold=cfg.signal_strength_threshold,
        )
        self.strategy.set_symbol(cfg.symbol)  # Set symbol for automated parameter tuning
        self.strategy.apply_config(cfg)
        self.strategy.update_timeframe(cfg.timeframe)
        self.strategy.set_signal_tracker(self._signal_tracker)
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
        self._reentry_blocked_until = 0.0
        self._prev_positions_count = 0
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
            price=float(self.status.mid or 0.0),
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
                    pos = self.exec.positions(self.cfg.symbol)
                    pos_count = len(pos)
                    if self._prev_positions_count > 0 and pos_count == 0:
                        # Position closed - record outcome for signal tracking
                        if hasattr(self, '_last_position_profit'):
                            self.strategy.record_trade_outcome(self._last_position_profit)
                            self._emit("INFO", f"Recorded trade outcome: profit={self._last_position_profit:.2f}")
                        self._reentry_blocked_until = time.time() + float(
                            self.cfg.reentry_cooldown_seconds
                        )
                        self._emit(
                            "INFO",
                            f"Re-entry cooldown {self.cfg.reentry_cooldown_seconds}s after position closed",
                        )
                    self._prev_positions_count = pos_count
                    self.status.positions_count = pos_count
                    self.status.positions_summary = self._format_positions(pos)
                    
                    # Track current position profit for outcome recording
                    if pos_count > 0:
                        current_profit = sum(float(getattr(p, "profit", 0.0) or 0.0) for p in pos)
                        self._last_position_profit = current_profit
                    else:
                        self._last_position_profit = 0.0
                    
                    if hasattr(self, "_trade_monitor") and self._trade_monitor:
                        self._trade_monitor.update_config(self.cfg)
                        self._trade_monitor.sync_positions_from_mt5(self.cfg.symbol)
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
                        min_win_rate=self.cfg.min_win_rate,
                    )
                    best = calibrate_best_params(hist, criteria=crit)
                    self.status.last_calibration_utc = utc_now().isoformat()
                    self._initial_calibrated = True

                    if best is None:
                        self.status.mode = "LIVE"  # Still trade with default parameters
                        self._emit("WARN", "No edge found - using default parameters for live trading")
                    else:
                        self._apply_calibrated(best)
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
                        min_win_rate=self.cfg.min_win_rate,
                    )
                    best = calibrate_best_params(hist, criteria=crit)
                    self.status.last_calibration_utc = utc_now().isoformat()
                    self._next_calibration_ts = time.time() + float(self.cfg.calibration_every_minutes) * 60.0

                    if best is None:
                        self.status.mode = "LIVE"  # Keep trading with current/default parameters
                        self._emit("WARN", "No edge found - continuing with current parameters")
                    else:
                        self._apply_calibrated(best)
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
                
                # Fetch higher timeframe data for multi-timeframe confirmation
                higher_tf_df = None
                if strategy.use_multi_timeframe_confirmation:
                    higher_timeframes = strategy._timeframe_hierarchy.get(self.cfg.timeframe, [])
                    if higher_timeframes:
                        try:
                            higher_tf = higher_timeframes[0]  # Use first higher timeframe
                            higher_tf_df = get_latest_candles(
                                self.client, self.cfg.symbol, higher_tf, n=self.cfg.candles
                            )
                            if not higher_tf_df.empty:
                                logger.debug(f"Multi-timeframe confirmation using {higher_tf}")
                        except Exception as e:
                            logger.debug(f"Failed to fetch higher timeframe data: {e}")
                
                sig = strategy.generate_signal(df_prep, higher_tf_df=higher_tf_df)
                self.status.last_signal = sig.value
                self._notify_changes()

                can_trade = time.time() >= self._reentry_blocked_until
                if (
                    can_trade
                    and sig in (Signal.BUY, Signal.SELL)
                    and not self.exec.has_position(self.cfg.symbol)
                ):
                    balance = float(self.status.balance or self.status.equity or 0.0)
                    atr_v = float(df_prep.iloc[-1].get("atr", 0.0))
                    direction = sig.value
                    entry = ask if direction == "BUY" else bid
                    # Apply currently active strategy ATR multiplier to give SL room per timeframe
                    try:
                        atr_mult = float(getattr(strategy, "atr_multiplier", 1.0) or 1.0)
                    except Exception:
                        atr_mult = 1.0
                    atr_for_sizing = float(atr_v) * atr_mult

                    # Volatility guard: skip entries when market volatility is excessive
                    vol = float(df_prep.iloc[-1].get("volatility", 0.0) or 0.0)
                    max_vol = float(getattr(self.cfg, "max_entry_volatility_percent", 0.0) or 0.0)
                    if max_vol > 0 and vol > max_vol:
                        self._emit(
                            "INFO",
                            f"Skipping entry due to high volatility {vol:.2f}% > {max_vol:.2f}%",
                        )
                        continue
                    # Dynamic position sizing for small and big trades
                    signal_confidence = self.strategy.get_signal_confidence()
                    plan = risk.plan_trade(
                        direction=direction,
                        entry=float(entry),
                        atr_value=float(atr_for_sizing),
                        balance=float(balance),
                        client=self.client,
                        symbol=self.cfg.symbol,
                        confidence=signal_confidence,  # Risk-based position sizing
                    )
                    
                    if plan.volume <= 0:
                        self._emit("WARN", "Signal but volume computed as 0; skipping order.")
                    else:
                        risk_budget = balance * self.cfg.risk_per_trade
                        loss_if_sl = money_at_risk(
                            self.client,
                            self.cfg.symbol,
                            float(entry),
                            plan.sl,
                            plan.volume,
                            plan.direction,
                        )
                        self._emit(
                            "INFO",
                            f"Trade plan: balance={balance:.2f} risk_budget={risk_budget:.2f} "
                            f"({self.cfg.risk_per_trade:.2%}) vol={plan.volume} "
                            f"entry={entry:.5f} sl={plan.sl:.5f} tp={plan.tp:.5f} "
                            f"loss_if_sl≈{loss_if_sl:.2f}",
                        )

                        # Paper trading: log signal without executing
                        if self.cfg.test_mode:
                            self._emit(
                                "INFO",
                                f"[PAPER TRADE] {plan.direction} {self.cfg.symbol} vol={plan.volume} "
                                f"entry={entry:.5f} sl={plan.sl:.5f} tp={plan.tp:.5f} "
                                f"risk_budget={risk_budget:.2f} loss_if_sl≈{loss_if_sl:.2f}",
                            )
                            # Track paper trade for later analysis
                            if not hasattr(self, '_paper_trades'):
                                self._paper_trades = []
                            self._paper_trades.append({
                                'time': utc_now(),
                                'symbol': self.cfg.symbol,
                                'direction': plan.direction,
                                'volume': plan.volume,
                                'entry': float(entry),
                                'sl': plan.sl,
                                'tp': plan.tp,
                                'risk_budget': risk_budget,
                                'loss_if_sl': loss_if_sl,
                            })
                        else:
                            res = self.exec.place_market_order(
                                symbol=self.cfg.symbol,
                                direction=plan.direction,
                                volume=plan.volume,
                                sl=plan.sl,
                                tp=plan.tp,
                            )
                        
                        if res.ok and hasattr(self, "_trade_monitor") and self._trade_monitor:
                            try:
                                ticket = self.exec.find_position_ticket(
                                    self.cfg.symbol, after_order=res.order_id
                                )
                                if ticket:
                                    actual_entry_price = float(entry)
                                    positions = self.exec.positions(symbol=self.cfg.symbol)
                                    position = next(
                                        (
                                            p
                                            for p in positions
                                            if int(getattr(p, "ticket", 0) or 0) == ticket
                                        ),
                                        None,
                                    )
                                    if position is not None:
                                        actual_entry_price = float(getattr(position, "price_open", entry) or entry)
                                        if abs(actual_entry_price - float(entry)) > 1e-9:
                                            logger.info(
                                                "Order filled at actual price %.5f (planned %.5f)",
                                                actual_entry_price,
                                                float(entry),
                                            )
                                    risk_money = money_at_risk(
                                        self.client,
                                        self.cfg.symbol,
                                        actual_entry_price,
                                        plan.sl,
                                        plan.volume,
                                        plan.direction,
                                    )
                                    self._trade_monitor.add_trade(
                                        ticket=ticket,
                                        symbol=self.cfg.symbol,
                                        trade_type=plan.direction,
                                        volume=plan.volume,
                                        entry_price=actual_entry_price,
                                        stop_loss=plan.sl,
                                        take_profit=plan.tp,
                                        initial_risk_money=risk_money,
                                    )
                            except Exception as e:
                                logger.error("Failed to add trade to monitor: %s", e)
                        
                        self._emit("INFO" if res.ok else "ERROR", res.message)
                        self._notify_changes()
                elif sig in (Signal.BUY, Signal.SELL) and not can_trade:
                    wait = int(self._reentry_blocked_until - time.time())
                    self._emit(
                        "INFO",
                        f"Signal {sig.value} — re-entry cooldown ({wait}s remaining)",
                    )
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
        
        # Generate paper trading report if in test mode
        if self.cfg.test_mode and hasattr(self, '_paper_trades') and self._paper_trades:
            self._generate_paper_trading_report()
        
        self._emit("INFO", "Bot stopped.")

    def _apply_calibrated(self, best: object) -> None:
        # Apply timeframe-specific settings first, then preserve the calibrated RSI/EMA values.
        self.strategy.update_timeframe(self.cfg.timeframe)
        self.strategy.rsi_period = best.rsi_period
        self.strategy.ema_period = best.ema_period
        self.risk = RiskManager(
            risk_per_trade=self.cfg.risk_per_trade,
            sl_atr_mult=best.sl_atr_mult,
            tp_rr=best.tp_rr,
        )
        logger.info(
            "Applied calibrated strategy: timeframe=%s rsi_period=%s ema_period=%s sl_atr_mult=%s tp_rr=%s",
            self.cfg.timeframe,
            best.rsi_period,
            best.ema_period,
            best.sl_atr_mult,
            best.tp_rr,
        )

    def _generate_paper_trading_report(self) -> None:
        """Generate a report of paper trading signals."""
        if not self._paper_trades:
            return
        
        report_lines = [
            "\n" + "=" * 80,
            "PAPER TRADING REPORT",
            "=" * 80,
            f"Symbol: {self.cfg.symbol}",
            f"Timeframe: {self.cfg.timeframe}",
            f"Total Signals: {len(self._paper_trades)}",
            f"Report Generated: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "-" * 80,
        ]
        
        # Calculate statistics
        buy_signals = sum(1 for t in self._paper_trades if t['direction'] == 'BUY')
        sell_signals = sum(1 for t in self._paper_trades if t['direction'] == 'SELL')
        avg_risk = sum(t['risk_budget'] for t in self._paper_trades) / len(self._paper_trades) if self._paper_trades else 0
        
        report_lines.extend([
            f"BUY Signals: {buy_signals}",
            f"SELL Signals: {sell_signals}",
            f"Average Risk per Signal: ${avg_risk:.2f}",
            "-" * 80,
            "Signal Details:",
        ])
        
        for i, trade in enumerate(self._paper_trades, 1):
            report_lines.append(
                f"{i}. {trade['time'].strftime('%Y-%m-%d %H:%M:%S')} | "
                f"{trade['direction']:4} | Entry: {trade['entry']:.5f} | "
                f"SL: {trade['sl']:.5f} | TP: {trade['tp']:.5f} | "
                f"Risk: ${trade['risk_budget']:.2f}"
            )
        
        report_lines.append("=" * 80)
        
        # Log the report
        report_text = "\n".join(report_lines)
        logger.info(report_text)
        self._emit("INFO", "Paper trading report generated. Check logs for details.")
        
        # Save to file
        try:
            import os
            reports_dir = "logs"
            if not os.path.exists(reports_dir):
                os.makedirs(reports_dir)
            
            report_file = os.path.join(reports_dir, f"paper_trading_{utc_now().strftime('%Y%m%d_%H%M%S')}.txt")
            with open(report_file, 'w') as f:
                f.write(report_text)
            logger.info(f"Paper trading report saved to: {report_file}")
        except Exception as e:
            logger.error(f"Failed to save paper trading report: {e}")

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
