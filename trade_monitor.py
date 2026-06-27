from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from .config import BotConfig, get_trail_params
    from .data import MT5Client, get_latest_candles
    from .execution import ExecutionEngine
    from .risk import money_at_risk, profit_at_price
    from .strategy import RsiEmaStrategy, Signal
    from .utils import setup_logging
except ImportError:
    from config import BotConfig, get_trail_params
    from data import MT5Client, get_latest_candles
    from execution import ExecutionEngine
    from risk import money_at_risk, profit_at_price
    from strategy import RsiEmaStrategy, Signal
    from utils import setup_logging

logger = setup_logging()


def _position_side(pos: Any) -> str:
    ptype = int(getattr(pos, "type", -1))
    return "BUY" if ptype == 0 else "SELL"


@dataclass
class OpenTrade:
    ticket: int
    symbol: str
    type: str
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: datetime
    initial_risk_money: float = 0.0
    trail_armed: bool = False
    breakeven_set: bool = False
    last_atr: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    duration_minutes: int = 0
    last_signal: Optional[Signal] = None
    market_condition: str = "UNKNOWN"

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.entry_time).total_seconds()

    def update_from_position(self, pos: Any, current_price: float) -> None:
        self.current_price = current_price
        self.unrealized_pnl = float(getattr(pos, "profit", 0.0) or 0.0)
        self.duration_minutes = int(
            (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 60
        )
        self.max_profit = max(self.max_profit, self.unrealized_pnl)
        self.max_loss = min(self.max_loss, self.unrealized_pnl)

    def _peak_threshold(self, cfg: BotConfig) -> float:
        if cfg.min_peak_profit_to_protect > 0:
            return cfg.min_peak_profit_to_protect
        if self.initial_risk_money > 0:
            return self.initial_risk_money * cfg.min_peak_profit_risk_fraction
        return 1.0

    def _min_giveback_drop(self, cfg: BotConfig) -> float:
        if cfg.profit_giveback_min_drop > 0:
            return cfg.profit_giveback_min_drop
        if self.initial_risk_money > 0:
            return self.initial_risk_money * cfg.profit_giveback_min_drop_risk_fraction
        return 0.5

    def should_close_for_profit_giveback(self, cfg: BotConfig) -> Tuple[bool, str]:
        """Close when profit has retreated from peak (even if still slightly positive)."""
        peak_threshold = self._peak_threshold(cfg)
        if self.max_profit < peak_threshold:
            return False, "Peak profit below protection threshold"

        drop = self.max_profit - self.unrealized_pnl
        if drop < self._min_giveback_drop(cfg):
            return False, "Giveback drop too small"

        if self.max_profit > 0:
            retained = self.unrealized_pnl / self.max_profit
            if retained < cfg.profit_giveback_ratio:
                return True, (
                    f"Profit giveback: peak {self.max_profit:.2f} -> now {self.unrealized_pnl:.2f} "
                    f"({retained:.0%} of peak)"
                )

        if (
            cfg.lock_profit_after_peak > 0
            and self.max_profit >= cfg.lock_profit_after_peak
            and self.unrealized_pnl < cfg.lock_profit_after_peak
        ):
            return True, (
                f"Profit lock: fell below {cfg.lock_profit_after_peak:.2f} "
                f"(peak was {self.max_profit:.2f})"
            )

        return False, "No giveback"

    def should_close_for_profit_protection(
        self, cfg: BotConfig, market_data: Dict
    ) -> Tuple[bool, str]:
        if self.unrealized_pnl <= 0:
            return False, "Not in profit"

        giveback, reason = self.should_close_for_profit_giveback(cfg)
        if giveback:
            return True, reason

        profit_amount = self.unrealized_pnl
        risk = self.initial_risk_money or 1.0
        profit_vs_risk = profit_amount / risk

        current_signal = market_data.get("signal", Signal.HOLD)
        rsi = float(market_data.get("rsi", 50))
        price = float(market_data.get("price", 0))
        ema = float(market_data.get("ema", 0))
        market_condition = market_data.get("condition", "UNKNOWN")
        
        # Stricter minimum for signal reversals (big profit threshold)
        min_r_big = 0.75  # need at least 75% of planned risk as profit for signal exits
        
        # Fallback minimum for small profit taking (when no big profit found)
        min_r_small = cfg.min_profit_percent_fallback / 100.0
        min_profit_threshold = (cfg.min_profit_percent_for_protection / 100.0)

        # Try to close on signal reversal with main threshold
        if self.type == "BUY" and current_signal == Signal.SELL and profit_vs_risk >= min_r_big:
            return True, f"Signal reversal to SELL — securing {profit_amount:.2f}"
        if self.type == "SELL" and current_signal == Signal.BUY and profit_vs_risk >= min_r_big:
            return True, f"Signal reversal to BUY — securing {profit_amount:.2f}"

        # Fallback: allow signal reversal with small profit if no big profit materialized
        if self.type == "BUY" and current_signal == Signal.SELL and profit_vs_risk >= min_r_small:
            return True, f"Signal reversal to SELL (small profit fallback) — securing {profit_amount:.2f}"
        if self.type == "SELL" and current_signal == Signal.BUY and profit_vs_risk >= min_r_small:
            return True, f"Signal reversal to BUY (small profit fallback) — securing {profit_amount:.2f}"

        # Check profit vs configured threshold for standard rules
        if profit_vs_risk < min_profit_threshold:
            return False, "Profit too small vs risk for protection rules"

        # Standard protection rules for profits meeting the threshold
        if profit_vs_risk >= 0.5:
            if self.type == "BUY" and market_condition == "DOWNTREND":
                return True, f"Downtrend — securing {profit_amount:.2f}"
            if self.type == "SELL" and market_condition == "UPTREND":
                return True, f"Uptrend — securing {profit_amount:.2f}"

        return False, "No profit protection needed"

    def should_close_for_loss_protection(
        self, cfg: BotConfig, market_data: Dict
    ) -> Tuple[bool, str]:
        # Hard maximum loss cap (absolute account percentage)
        if hasattr(cfg, 'max_loss_per_trade') and cfg.max_loss_per_trade > 0:
            account_balance = self.initial_risk_money / cfg.risk_per_trade if cfg.risk_per_trade > 0 else 0
            if account_balance > 0:
                max_allowed_loss = account_balance * cfg.max_loss_per_trade
                if abs(self.unrealized_pnl) > max_allowed_loss:
                    return True, (
                        f"Hard loss cap exceeded: {abs(self.unrealized_pnl):.2f} > {max_allowed_loss:.2f} "
                        f"({cfg.max_loss_per_trade:.1%} of account)"
                    )

        # Was profitable, now giving back into loss
        if (
            self.max_profit >= self._peak_threshold(cfg)
            and self.unrealized_pnl <= 0
            and self.age_seconds() >= cfg.monitor_grace_seconds
        ):
            return True, (
                f"Peak profit {self.max_profit:.2f} lost — now {self.unrealized_pnl:.2f}"
            )

        if self.unrealized_pnl >= 0:
            return False, "Trade not losing"

        loss_amount = abs(self.unrealized_pnl)
        risk_money = self.initial_risk_money
        if risk_money <= 0:
            return False, "Risk not computed yet"

        rsi = float(market_data.get("rsi", 50))
        volatility = float(market_data.get("volatility", 0))

        if loss_amount > risk_money * cfg.loss_risk_close_ratio:
            return True, (
                f"Loss exceeded {cfg.loss_risk_close_ratio:.0%} of risk "
                f"({loss_amount:.2f} / {risk_money:.2f})"
            )

        if self.duration_minutes > cfg.loss_timeout_minutes:
            return True, f"Trade timeout after {self.duration_minutes} minutes"

        if volatility > 1.5 and loss_amount > risk_money * 0.5:
            return True, f"High volatility with loss ({loss_amount:.2f})"

        if self.type == "BUY" and rsi < 25 and loss_amount > risk_money * 0.5:
            return True, f"Strong bearish RSI ({rsi:.1f})"
        if self.type == "SELL" and rsi > 75 and loss_amount > risk_money * 0.5:
            return True, f"Strong bullish RSI ({rsi:.1f})"

        return False, "No loss protection needed"


@dataclass
class TradeDecision:
    action: str
    reason: str
    new_stop_loss: Optional[float] = None
    confidence: float = 0.0


class TradeMonitor:
    def __init__(
        self,
        client: MT5Client,
        execution_engine: ExecutionEngine,
        cfg: BotConfig,
        on_position_closed: Optional[Callable[[], None]] = None,
    ):
        self.client = client
        self.execution = execution_engine
        self.cfg = cfg
        self.on_position_closed = on_position_closed
        self.open_trades: Dict[int, OpenTrade] = {}
        self.strategy = RsiEmaStrategy(
            use_ml_confirmation=cfg.use_ml_confirmation,
            ml_min_train_rows=cfg.ml_min_train_rows,
        )
        self.strategy.apply_config(cfg)
        tf = cfg.monitor_timeframe.strip() or cfg.timeframe
        self.strategy.update_timeframe(tf)

    def update_config(self, cfg: BotConfig) -> None:
        self.cfg = cfg
        self.strategy.apply_config(cfg)
        tf = cfg.monitor_timeframe.strip() or cfg.timeframe
        self.strategy.update_timeframe(tf)
        if (cfg.trade_management_mode or "").lower() == "trail":
            tp = get_trail_params(tf, cfg)
            logger.info(
                "Trail [%s]: activate=%.2f×risk break-even=%.2f×risk "
                "ATR trail=%.1f× grace=%ss",
                tf,
                tp.trail_activate_risk_multiple,
                tp.breakeven_risk_multiple,
                tp.trail_atr_mult,
                tp.monitor_grace_seconds,
            )

    def sync_positions_from_mt5(self, symbol: Optional[str] = None) -> None:
        """Ensure all MT5 positions for our magic are tracked."""
        try:
            positions = self.execution.positions(symbol=symbol or None)
        except Exception as e:
            logger.debug("sync_positions_from_mt5 failed: %s", e)
            return

        seen: set[int] = set()
        for pos in positions:
            magic = int(getattr(pos, "magic", 0) or 0)
            if magic and magic != self.cfg.magic_number:
                continue
            ticket = int(getattr(pos, "ticket", 0) or 0)
            if not ticket:
                continue
            seen.add(ticket)
            sym = str(getattr(pos, "symbol", ""))
            if symbol and sym != symbol:
                continue

            side = _position_side(pos)
            entry = float(getattr(pos, "price_open", 0.0) or 0.0)
            sl = float(getattr(pos, "sl", 0.0) or 0.0)
            tp = float(getattr(pos, "tp", 0.0) or 0.0)
            vol = float(getattr(pos, "volume", 0.0) or 0.0)
            ts_open = int(getattr(pos, "time", 0) or 0)
            entry_time = (
                datetime.fromtimestamp(ts_open, tz=timezone.utc)
                if ts_open
                else datetime.now(timezone.utc)
            )

            if ticket in self.open_trades:
                trade = self.open_trades[ticket]
                if abs(trade.entry_price - entry) > 1e-9:
                    logger.info(
                        "Syncing trade ticket=%s entry_price %.5f -> %.5f from MT5",
                        ticket,
                        trade.entry_price,
                        entry,
                    )
                    trade.entry_price = entry
                trade.stop_loss = sl or trade.stop_loss
                trade.take_profit = tp or trade.take_profit
            else:
                risk_money = money_at_risk(self.client, sym, entry, sl, vol, side)
                self.open_trades[ticket] = OpenTrade(
                    ticket=ticket,
                    symbol=sym,
                    type=side,
                    volume=vol,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    entry_time=entry_time,
                    initial_risk_money=risk_money,
                )
                logger.info("Tracking MT5 position ticket=%s %s %s", ticket, side, sym)

        for ticket in list(self.open_trades.keys()):
            if ticket not in seen:
                del self.open_trades[ticket]

    def add_trade(
        self,
        ticket: int,
        symbol: str,
        trade_type: str,
        volume: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        initial_risk_money: float = 0.0,
    ) -> None:
        if initial_risk_money <= 0:
            initial_risk_money = money_at_risk(
                self.client, symbol, entry_price, stop_loss, volume, trade_type
            )
        self.open_trades[ticket] = OpenTrade(
            ticket=ticket,
            symbol=symbol,
            type=trade_type.upper(),
            volume=volume,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=datetime.now(timezone.utc),
            initial_risk_money=initial_risk_money,
        )
        logger.info("Added trade ticket=%s %s %s @ %s", ticket, trade_type, symbol, entry_price)

    def remove_trade(self, ticket: int) -> None:
        if ticket in self.open_trades:
            trade = self.open_trades[ticket]
            logger.info(
                "Removed trade ticket=%s %s — P&L: %.2f",
                ticket,
                trade.symbol,
                trade.unrealized_pnl,
            )
            del self.open_trades[ticket]

    def update_all_trades(self) -> List[Tuple[int, TradeDecision]]:
        self.sync_positions_from_mt5(self.cfg.symbol.strip() or None)
        decisions: List[Tuple[int, TradeDecision]] = []

        for ticket, trade in list(self.open_trades.items()):
            try:
                positions = self.execution.positions(symbol=trade.symbol)
                pos = next(
                    (p for p in positions if int(getattr(p, "ticket", 0)) == ticket),
                    None,
                )
                if pos is None:
                    self.remove_trade(ticket)
                    continue

                tick = self.client.tick(trade.symbol)
                # Bid for longs, ask for shorts — matches MT5 profit direction
                current_price = float(
                    tick.bid if trade.type == "BUY" else tick.ask
                )
                trade.update_from_position(pos, current_price)

                market_data = self._get_market_analysis(trade.symbol)
                trade.market_condition = market_data["condition"]
                trade.last_signal = market_data["signal"]

                decision = self._analyze_trade(trade, market_data)
                logger.info(
                    "ticket=%s decision=%s reason=%s pnl=%.2f max_profit=%.2f current_price=%.5f stop_loss=%.5f tp=%.5f trail_armed=%s breakeven_set=%s",
                    ticket,
                    decision.action,
                    decision.reason,
                    trade.unrealized_pnl,
                    trade.max_profit,
                    trade.current_price,
                    trade.stop_loss,
                    trade.take_profit,
                    trade.trail_armed,
                    trade.breakeven_set,
                )
                decisions.append((ticket, decision))
            except Exception as e:
                logger.error("Error updating trade %s: %s", ticket, e)
                decisions.append((ticket, TradeDecision("HOLD", f"Error: {e}")))

        return decisions

    def _monitor_timeframe(self) -> str:
        return self.cfg.monitor_timeframe.strip() or self.cfg.timeframe

    def _get_market_analysis(self, symbol: str) -> Dict:
        try:
            df = get_latest_candles(
                self.client,
                symbol,
                self._monitor_timeframe(),
                n=self.cfg.monitor_candles,
            )
            if df.empty:
                return self._empty_market_data()

            df_prepared = self.strategy.prepare(df)
            signal = self.strategy.generate_signal(df_prepared)
            last = df_prepared.iloc[-1]
            rsi = float(last["rsi"])
            price = float(last["close"])
            ema_v = float(last["ema"])
            volatility = float(last.get("volatility", 0))
            price_position = float(last.get("price_position", 0.5))
            resistance = float(last.get("resistance", price * 1.02))
            support = float(last.get("support", price * 0.98))

            if rsi < 35:
                condition = "OVERSOLD"
            elif rsi > 65:
                condition = "OVERBOUGHT"
            elif price > ema_v:
                condition = "UPTREND"
            elif price < ema_v:
                condition = "DOWNTREND"
            else:
                condition = "SIDEWAYS"

            atr_v = float(last.get("atr", 0.0) or 0.0)

            return {
                "condition": condition,
                "signal": signal,
                "rsi": rsi,
                "price": price,
                "ema": ema_v,
                "atr": atr_v,
                "volatility": volatility,
                "price_position": price_position,
                "resistance": resistance,
                "support": support,
            }
        except Exception as e:
            logger.error("Market analysis failed: %s", e)
            return self._empty_market_data()

    @staticmethod
    def _empty_market_data() -> Dict:
        return {
            "condition": "NO_DATA",
            "signal": Signal.HOLD,
            "rsi": 50.0,
            "price": 0.0,
            "ema": 0.0,
            "atr": 0.0,
            "volatility": 0.0,
            "price_position": 0.5,
            "resistance": 0.0,
            "support": 0.0,
        }

    def _trail_settings(self) -> object:
        return get_trail_params(self._monitor_timeframe(), self.cfg)

    def _analyze_trade(self, trade: OpenTrade, market_data: Dict) -> TradeDecision:
        cfg = self.cfg
        mode = (cfg.trade_management_mode or "trail").lower()
        trade.last_atr = float(market_data.get("atr", 0.0) or 0.0)

        if mode == "broker_only":
            return TradeDecision(
                "HOLD",
                f"broker_only — MT5 SL/TP manage exit (pnl={trade.unrealized_pnl:.2f})",
                confidence=1.0,
            )

        trail_cfg = self._trail_settings()
        grace_sec = trail_cfg.monitor_grace_seconds
        in_grace = trade.age_seconds() < grace_sec
        if in_grace:
            logger.info(
                "ticket=%s trail hold: grace period %ss (age=%ss) -> HOLD",
                trade.ticket,
                grace_sec,
                int(trade.age_seconds()),
            )
            return TradeDecision(
                "HOLD",
                f"Grace period ({int(trade.age_seconds())}s / {grace_sec}s)",
                confidence=0.9,
            )

        if mode == "trail":
            return self._analyze_trail_mode(trade, market_data, trail_cfg)

        if mode == "giveback":
            giveback, reason = trade.should_close_for_profit_giveback(cfg)
            if giveback:
                return TradeDecision("CLOSE", reason, confidence=0.95)
            
            # Also check for small profit taking on signal reversal (new fallback)
            if trade.unrealized_pnl > 0:
                should_close, reason = trade.should_close_for_profit_protection(cfg, market_data)
                if should_close:
                    return TradeDecision("CLOSE", reason, confidence=0.85)
            
            if trade.unrealized_pnl < 0 and trade.initial_risk_money > 0:
                loss_amount = abs(trade.unrealized_pnl)
                if loss_amount > trade.initial_risk_money * cfg.loss_risk_close_ratio:
                    return TradeDecision(
                        "CLOSE",
                        f"Loss exceeded {cfg.loss_risk_close_ratio:.0%} of risk",
                        confidence=0.9,
                    )
            return TradeDecision("HOLD", "giveback mode — holding", confidence=0.5)

        # full — all legacy rules (not recommended for live)
        giveback, reason = trade.should_close_for_profit_giveback(cfg)
        if giveback:
            return TradeDecision("CLOSE", reason, confidence=0.95)

        if trade.unrealized_pnl > 0:
            should_close, reason = trade.should_close_for_profit_protection(cfg, market_data)
            if should_close:
                return TradeDecision("CLOSE", reason, confidence=0.85)

        should_close, reason = trade.should_close_for_loss_protection(cfg, market_data)
        if should_close:
            return TradeDecision("CLOSE", reason, confidence=0.9)

        if trade.duration_minutes > cfg.max_trade_duration_minutes:
            return TradeDecision(
                "CLOSE",
                f"Max duration ({trade.duration_minutes} min)",
                confidence=0.7,
            )

        decision = self._analyze_trailing_stop(trade, market_data)
        if decision.action != "HOLD":
            return decision

        return TradeDecision("HOLD", "No action", confidence=0.5)

    def _analyze_trail_mode(self, trade: OpenTrade, market_data: Dict, trail_cfg: object) -> TradeDecision:
        """ATR trail + optional break-even; exits only via modified SL / broker TP (no monitor CLOSE)."""
        risk = trade.initial_risk_money
        if risk <= 0:
            return TradeDecision("HOLD", "Risk not computed", confidence=0.5)

        pnl = trade.unrealized_pnl
        atr_v = trade.last_atr
        if atr_v <= 0:
            atr_v = abs(trade.entry_price) * 0.001

        # 1) Break-even: move SL to entry once profit threshold met
        be_mult = float(getattr(trail_cfg, "breakeven_risk_multiple", 0.0) or 0.0)
        if be_mult > 0 and not trade.breakeven_set and pnl >= risk * be_mult:
            new_sl = trade.entry_price
            if self._should_move_sl(trade, new_sl):
                trade.breakeven_set = True
                return TradeDecision(
                    "MODIFY_SL",
                    f"Break-even SL @ {new_sl:.5f} (pnl={pnl:.2f})",
                    new_stop_loss=new_sl,
                    confidence=0.85,
                )

        # 2) Arm ATR trail after activation profit (× planned risk)
        activate_mult = float(trail_cfg.trail_activate_risk_multiple)
        if pnl >= risk * activate_mult:
            if not trade.trail_armed:
                logger.info(
                    "ticket=%s trail armed at pnl=%.2f threshold=%.2f",
                    trade.ticket,
                    pnl,
                    risk * activate_mult,
                )
            trade.trail_armed = True

        if not trade.trail_armed:
            logger.info(
                "ticket=%s trail hold: pnl=%.2f below arm threshold %.2f -> HOLD",
                trade.ticket,
                pnl,
                risk * activate_mult,
            )
            return TradeDecision(
                "HOLD",
                f"Trail pending (pnl={pnl:.2f}, need {risk * activate_mult:.2f})",
                confidence=0.5,
            )

        atr_mult = float(trail_cfg.trail_atr_mult)
        trail_dist = atr_v * atr_mult

        if trade.type == "BUY":
            new_stop = trade.current_price - trail_dist
        else:
            new_stop = trade.current_price + trail_dist

        if self._should_move_sl(trade, new_stop):
            return TradeDecision(
                "MODIFY_SL",
                f"ATR trail ({atr_mult}×ATR={trail_dist:.5f}) SL→{new_stop:.5f}",
                new_stop_loss=new_stop,
                confidence=0.8,
            )

        logger.info(
            "ticket=%s trail armed but no SL update: current_price=%.5f new_stop=%.5f existing_sl=%.5f",
            trade.ticket,
            trade.current_price,
            new_stop,
            trade.stop_loss,
        )
        return TradeDecision("HOLD", f"ATR trail armed (pnl={pnl:.2f})", confidence=0.5)

    @staticmethod
    def _should_move_sl(trade: OpenTrade, new_stop: float) -> bool:
        if trade.type == "BUY":
            if not trade.stop_loss:
                return True
            return new_stop > trade.stop_loss + 1e-9
        if not trade.stop_loss:
            return True
        return new_stop < trade.stop_loss - 1e-9

    def _analyze_trailing_stop(self, trade: OpenTrade, market_data: Dict) -> TradeDecision:
        """Legacy percent trail for full mode."""
        if trade.unrealized_pnl <= 0:
            return TradeDecision("HOLD", "No trail while losing", confidence=0.5)

        atr_v = float(market_data.get("atr", 0.0) or 0.0)
        if atr_v > 0:
            trail_cfg = self._trail_settings()
            trail_dist = atr_v * trail_cfg.trail_atr_mult
        else:
            trail_dist = trade.entry_price * (self.cfg.trailing_stop_percent / 100.0)

        if trade.type == "BUY":
            new_stop = trade.current_price - trail_dist
            if self._should_move_sl(trade, new_stop):
                return TradeDecision(
                    "MODIFY_SL",
                    f"Trailing stop {new_stop:.5f}",
                    new_stop_loss=new_stop,
                    confidence=0.8,
                )
        else:
            new_stop = trade.current_price + trail_dist
            if self._should_move_sl(trade, new_stop):
                return TradeDecision(
                    "MODIFY_SL",
                    f"Trailing stop {new_stop:.5f}",
                    new_stop_loss=new_stop,
                    confidence=0.8,
                )
        return TradeDecision("HOLD", "No trail change", confidence=0.5)

    def execute_decision(self, ticket: int, decision: TradeDecision) -> bool:
        if ticket not in self.open_trades:
            return False
        trade = self.open_trades[ticket]

        try:
            if decision.action == "CLOSE":
                close_dir = "SELL" if trade.type == "BUY" else "BUY"
                result = self.execution.close_position(ticket, close_dir)
                if result.ok:
                    logger.info(
                        "Closed ticket=%s: %s — P&L %.2f",
                        ticket,
                        decision.reason,
                        trade.unrealized_pnl,
                    )
                    self.remove_trade(ticket)
                    if self.on_position_closed:
                        try:
                            self.on_position_closed()
                        except Exception as e:
                            logger.debug("on_position_closed callback failed: %s", e)
                    return True
                logger.error("Close failed ticket=%s: %s", ticket, result.message)
                return False

            if decision.action == "MODIFY_SL" and decision.new_stop_loss is not None:
                result = self.execution.modify_stop_loss(ticket, decision.new_stop_loss)
                if result.ok:
                    trade.stop_loss = decision.new_stop_loss
                    logger.info(
                        "ticket=%s %s",
                        ticket,
                        decision.reason,
                    )
                    return True
                logger.error("Modify SL failed ticket=%s: %s", ticket, result.message)
                return False
        except Exception as e:
            logger.error("execute_decision ticket=%s: %s", ticket, e)
        return False

    def get_trade_summary(self) -> Dict:
        if not self.open_trades:
            return {"total_trades": 0, "total_pnl": 0.0, "trades": []}

        rows = []
        total = 0.0
        for t in self.open_trades.values():
            stop_loss_value = money_at_risk(
                self.client,
                t.symbol,
                t.entry_price,
                t.stop_loss,
                t.volume,
                t.type,
            )
            take_profit_value = profit_at_price(
                self.client,
                t.symbol,
                t.type,
                t.volume,
                t.entry_price,
                t.take_profit,
            )
            rows.append(
                {
                    "ticket": t.ticket,
                    "symbol": t.symbol,
                    "type": t.type,
                    "volume": t.volume,
                    "entry_price": t.entry_price,
                    "current_price": t.current_price,
                    "unrealized_pnl": t.unrealized_pnl,
                    "max_profit": t.max_profit,
                    "duration_minutes": t.duration_minutes,
                    "market_condition": t.market_condition,
                    "last_signal": t.last_signal.value if t.last_signal else "NONE",
                    "stop_loss_value": stop_loss_value,
                    "take_profit_value": take_profit_value,
                }
            )
            total += t.unrealized_pnl
        return {"total_trades": len(rows), "total_pnl": total, "trades": rows}
