from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, Dict

import numpy as np
import pandas as pd

try:
    from .utils import setup_logging
except ImportError:
    from utils import setup_logging

try:
    from market_analysis import MarketAnalyzer, MarketAnalysis, TrendDirection
    from trade_scorer import TradeScorer, TradeScore
    from multi_timeframe import MultiTimeframeAnalyzer, MultiTimeframeSignal
except ImportError:
    # Fallback if new modules not available
    MarketAnalyzer = None
    TradeScorer = None
    MultiTimeframeAnalyzer = None

logger = setup_logging()


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands"""
    close = df["close"].astype(float)
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return upper, sma, lower


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> tuple[pd.Series, pd.Series]:
    """Calculate Stochastic Oscillator"""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    
    k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
    d_percent = k_percent.rolling(window=d_period).mean()
    
    return k_percent, d_percent


class BaseStrategy:
    name: str = "BaseStrategy"

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        raise NotImplementedError


@dataclass
class MLConfirmation:
    """Optional lightweight confirmation model.

    Trains a logistic regression on simple features. If not enough data, it returns None.
    """

    min_train_rows: int = 500
    threshold: float = 0.55

    def __post_init__(self) -> None:
        self._model = None
        self._trained = False

    @staticmethod
    def _features(df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"].astype(float)
        feat = pd.DataFrame(index=df.index)
        feat["ret1"] = close.pct_change()
        feat["ret5"] = close.pct_change(5)
        feat["rsi14"] = df["rsi"].astype(float)
        feat["dist_ema"] = (close - df["ema"]).astype(float) / close.replace(0.0, np.nan)
        feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
        return feat

    def fit(self, df: pd.DataFrame) -> bool:
        try:
            from sklearn.linear_model import LogisticRegression
        except Exception as e:  # pragma: no cover
            logger.warning("scikit-learn unavailable, ML confirmation disabled: %s", e)
            return False

        feat = self._features(df)
        if len(feat) < self.min_train_rows:
            return False

        # label: next bar direction
        close = df.loc[feat.index, "close"].astype(float)
        y = (close.shift(-1) > close).astype(int).loc[feat.index].dropna()
        X = feat.loc[y.index]
        if len(X) < self.min_train_rows:
            return False

        model = LogisticRegression(max_iter=500)
        model.fit(X.values, y.values)
        self._model = model
        self._trained = True
        return True

    def confirm(self, df: pd.DataFrame, desired: Signal) -> Optional[bool]:
        if not self._trained or self._model is None:
            return None
        feat = self._features(df)
        if feat.empty:
            return None
        x = feat.iloc[-1].values.reshape(1, -1)
        p_up = float(self._model.predict_proba(x)[0, 1])
        if desired == Signal.BUY:
            return p_up >= self.threshold
        if desired == Signal.SELL:
            return (1 - p_up) >= self.threshold
        return None


@dataclass
class RsiEmaStrategy(BaseStrategy):
    name: str = "RSI+EMA Enhanced"
    use_ml_confirmation: bool = False
    ml_min_train_rows: int = 500
    signal_strength_threshold: float = 2.0  # Balanced threshold for quality vs quantity
    use_multi_timeframe_confirmation: bool = True  # Require higher timeframe confirmation

    # Timeframe-specific parameters
    timeframe_params: Dict[str, Dict] = None

    def apply_config(self, cfg: object) -> None:
        """Apply BotConfig strategy fields (does not change timeframe presets)."""
        self.rsi_period = int(getattr(cfg, "rsi_period", self.rsi_period))
        self.ema_period = int(getattr(cfg, "ema_period", self.ema_period))
        self.rsi_buy = float(getattr(cfg, "rsi_buy", self.rsi_buy))
        self.rsi_sell = float(getattr(cfg, "rsi_sell", self.rsi_sell))
        self.signal_strength_threshold = float(
            getattr(cfg, "signal_strength_threshold", self.signal_strength_threshold)
        )
        self.use_multi_timeframe_confirmation = bool(
            getattr(cfg, "use_multi_timeframe_confirmation", self.use_multi_timeframe_confirmation)
        )
        use_ml = bool(getattr(cfg, "use_ml_confirmation", self.use_ml_confirmation))
        if use_ml != self.use_ml_confirmation:
            self.use_ml_confirmation = use_ml
            self._ml = (
                MLConfirmation(min_train_rows=self.ml_min_train_rows)
                if use_ml
                else None
            )

    def __post_init__(self) -> None:
        # Default timeframe-specific parameters
        self.timeframe_params = self.timeframe_params or {
            "M1": {"rsi_period": 14, "ema_period": 100, "rsi_buy": 25, "rsi_sell": 75, "atr_multiplier": 2.0},
            "M5": {"rsi_period": 14, "ema_period": 200, "rsi_buy": 30, "rsi_sell": 70, "atr_multiplier": 2.5},
            "M15": {"rsi_period": 14, "ema_period": 200, "rsi_buy": 30, "rsi_sell": 70, "atr_multiplier": 3.0},
            "M30": {"rsi_period": 21, "ema_period": 200, "rsi_buy": 35, "rsi_sell": 65, "atr_multiplier": 3.5},
            "H1": {"rsi_period": 21, "ema_period": 200, "rsi_buy": 35, "rsi_sell": 65, "atr_multiplier": 4.0},
            "H4": {"rsi_period": 21, "ema_period": 100, "rsi_buy": 40, "rsi_sell": 60, "atr_multiplier": 4.5},
            "D1": {"rsi_period": 30, "ema_period": 50, "rsi_buy": 40, "rsi_sell": 60, "atr_multiplier": 5.0}
        }
        
        # Current timeframe parameters (will be updated in prepare)
        self.current_timeframe = "M5"  # Default
        self.rsi_period = 14
        self.ema_period = 200
        self.rsi_buy = 30.0
        self.rsi_sell = 70.0
        self.atr_multiplier = 2.5
        
        self._ml = (
            MLConfirmation(min_train_rows=self.ml_min_train_rows)
            if self.use_ml_confirmation
            else None
        )
        
        # Trend analysis state
        self._trend_direction = None
        self._last_signal = Signal.HOLD
        self._signal_count = 0
        
        # Multi-timeframe hierarchy
        self._timeframe_hierarchy = {
            "M1": ["M5", "M15"],
            "M5": ["M15", "H1"],
            "M15": ["H1", "H4"],
            "M30": ["H1", "H4"],
            "H1": ["H4", "D1"],
            "H4": ["D1"],
            "D1": []
        }
        self._higher_timeframe_signal = Signal.HOLD
        
        # Initialize new analysis components if available
        self._market_analyzer = MarketAnalyzer() if MarketAnalyzer else None
        self._trade_scorer = TradeScorer(min_score=48.0, signal_tracker=None) if TradeScorer else None  # Lowered to 40% for more signals
        self._mtf_analyzer = MultiTimeframeAnalyzer() if MultiTimeframeAnalyzer else None
        self._signal_tracker = None  # Will be set by main bot
        self._current_symbol = None  # For symbol-specific parameter tuning

    def set_signal_tracker(self, signal_tracker) -> None:
        """Set the signal tracker for performance tracking"""
        self._signal_tracker = signal_tracker
        self._last_signal_direction = None
        self._last_signal_reasons = []
        # Update trade scorer with signal tracker
        if self._trade_scorer:
            self._trade_scorer.signal_tracker = signal_tracker

    def _safe_ratio(
        self, numerator: pd.Series, denominator: pd.Series, default: float = 0.5
    ) -> pd.Series:
        result = numerator.div(denominator.replace(0.0, np.nan))
        return result.fillna(default)

    @staticmethod
    def _reason_indicator_type(reason: str) -> str:
        reason = reason.lower()
        if "rsi" in reason or "macd" in reason or "stochastic" in reason:
            return "momentum"
        if "ema" in reason:
            return "trend"
        if "bb" in reason or "bollinger" in reason:
            return "volatility"
        if "volume" in reason:
            return "volume"
        if "support" in reason or "resistance" in reason:
            return "price_level"
        return "other"

    @staticmethod
    def _score_reason(reason: str) -> int:
        text = reason.lower()
        if any(x in text for x in [
            "overbought rsi",
            "oversold rsi",
            "ema bullish crossover",
            "ema bearish crossover",
        ]):
            return 3
        if any(x in text for x in [
            "uptrend pullback",
            "downtrend pullback",
            "macd bullish",
            "macd bearish",
            "near support",
            "near resistance",
            "extreme rsi",
            "bb bounce",
            "stochastic",
        ]):
            return 2
        if "volume" in text:
            return 1
        return 0

    def _score_reasons(self, reasons: list[str]) -> int:
        return sum(self._score_reason(reason) for reason in reasons)

    def _attempt_train_ml(self, df: pd.DataFrame) -> None:
        if self._ml is not None and not self._ml._trained:
            self._ml.fit(df)

    def set_symbol(self, symbol: str) -> None:
        """Set the current trading symbol for parameter tuning"""
        self._current_symbol = symbol

    def _get_symbol_volatility_baseline(self) -> Optional[float]:
        """Get baseline volatility for the current symbol for automated parameter tuning"""
        # Symbol-specific volatility baselines (typical ATR percentage)
        volatility_baselines = {
            "XRPUSD": 0.8,   # High volatility crypto
            "BTCUSD": 0.6,   # High volatility crypto
            "ETHUSD": 0.5,   # Medium-high volatility crypto
            "EURUSD": 0.1,   # Low volatility major pair
            "GBPUSD": 0.12,  # Low volatility major pair
            "USDJPY": 0.08,  # Very low volatility major pair
            "USDCHF": 0.09,  # Low volatility major pair
            "AUDUSD": 0.11,  # Low volatility major pair
            "NZDUSD": 0.10,  # Low volatility major pair
            "USDCAD": 0.11,  # Low volatility major pair
        }
        
        # Try to get symbol from timeframe data or use a default
        symbol = getattr(self, '_current_symbol', None)
        if symbol:
            return volatility_baselines.get(symbol, 0.2)  # Default to 0.2 for unknown symbols
        
        return 0.2  # Default baseline
    
    def _detect_market_regime(self, df: pd.DataFrame) -> str:
        """Detect current market regime for automated parameter adjustment"""
        if len(df) < 50:
            return "UNKNOWN"
        
        # Calculate key metrics
        close = df['close'].values
        atr = df['atr'].values[-1] if 'atr' in df.columns else 0
        close_price = close[-1]
        
        # Calculate price trend
        returns = np.diff(np.log(close[-50:]))
        trend_strength = np.mean(returns) * 100  # Percentage trend
        
        # Calculate volatility
        volatility = np.std(returns) * 100
        
        # Determine regime
        if abs(trend_strength) > 0.5 and volatility > 0.3:
            return "TRENDING_HIGH_VOL"
        elif abs(trend_strength) > 0.3:
            return "TRENDING_LOW_VOL"
        elif volatility > 0.5:
            return "CHOPPY_HIGH_VOL"
        elif volatility < 0.1:
            return "FLAT"
        else:
            return "RANGING"
    
    def record_trade_outcome(self, profit: float) -> None:
        """Record the outcome of a trade for signal performance tracking"""
        if self._signal_tracker and self._last_signal_direction and self._last_signal_reasons:
            # Record outcome for each signal reason
            for reason in self._last_signal_reasons:
                self._signal_tracker.record_trade_outcome(reason, profit)
            # Also record for the overall direction
            self._signal_tracker.record_trade_outcome(self._last_signal_direction, profit)
            logger.info(f"Recorded trade outcome: {self._last_signal_direction} profit={profit:.2f} for {len(self._last_signal_reasons)} signals")
            # Reset
            self._last_signal_direction = None
            self._last_signal_reasons = []
    
    def get_signal_confidence(self) -> float:
        """Get confidence level of last signal (0.0 to 1.0) for risk-based position sizing"""
        if not self._last_signal_direction or not self._last_signal_reasons:
            return 0.5  # Default confidence
        
        # Calculate confidence based on:
        # 1. Number of signal reasons (more reasons = higher confidence)
        # 2. Diversity of indicator types
        # 3. Historical performance of signals
        
        num_reasons = len(self._last_signal_reasons)
        confidence = min(num_reasons / 5.0, 1.0)  # Max confidence at 5+ reasons
        
        # Add bonus for indicator diversity
        indicator_types = set()
        for reason in self._last_signal_reasons:
            if "RSI" in reason or "MACD" in reason or "Stochastic" in reason:
                indicator_types.add("momentum")
            elif "EMA" in reason:
                indicator_types.add("trend")
            elif "BB" in reason:
                indicator_types.add("volatility")
            elif "volume" in reason.lower():
                indicator_types.add("volume")
        
        diversity_bonus = len(indicator_types) * 0.1
        confidence = min(confidence + diversity_bonus, 1.0)
        
        # Adjust based on historical performance if available
        if self._signal_tracker:
            proven_count = 0
            for reason in self._last_signal_reasons:
                if self._signal_tracker.is_signal_proven(reason, min_trades=3, min_win_rate=35.0, min_profit_factor=1.0):
                    proven_count += 1
            
            if num_reasons > 0:
                proven_ratio = proven_count / num_reasons
                confidence = confidence * (0.5 + 0.5 * proven_ratio)  # Weight by proven ratio
        
        return max(confidence, 0.1)  # Minimum 10% confidence
        
    def update_timeframe(self, timeframe: str) -> None:
        """Update strategy parameters for new timeframe"""
        if timeframe in self.timeframe_params:
            params = self.timeframe_params[timeframe]
            self.current_timeframe = timeframe
            self.rsi_period = params["rsi_period"]
            self.ema_period = params["ema_period"]
            self.rsi_buy = params["rsi_buy"]
            self.rsi_sell = params["rsi_sell"]
            self.atr_multiplier = params["atr_multiplier"]
            logger.info(f"Strategy updated for {timeframe}: RSI={self.rsi_period}, EMA={self.ema_period}")
        else:
            logger.warning(f"Unknown timeframe {timeframe}, using default parameters")

    def check_higher_timeframe_signal(self, higher_tf_df: pd.DataFrame) -> Signal:
        """Generate signal from higher timeframe for confirmation"""
        if higher_tf_df.empty or len(higher_tf_df) < max(self.ema_period, self.rsi_period) + 10:
            return Signal.HOLD
        
        # Use same logic but with higher timeframe data
        df_prep = self.prepare(higher_tf_df)
        last = df_prep.iloc[-1]
        
        rsi_v = float(last["rsi"])
        price_above_ema = bool(last["price_above_ema"])
        ema_trend = float(last["ema_trend"])
        macd_v = float(last["macd"])
        macd_signal_v = float(last["macd_signal"])
        
        # Simple but effective higher timeframe confirmation
        if rsi_v < self.rsi_buy and price_above_ema and macd_v > macd_signal_v:
            return Signal.BUY
        elif rsi_v > self.rsi_sell and not price_above_ema and macd_v < macd_signal_v:
            return Signal.SELL
        
        return Signal.HOLD

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        
        # Basic indicators
        df["ema"] = ema(df["close"].astype(float), self.ema_period)
        df["rsi"] = rsi(df["close"].astype(float), self.rsi_period)
        df["atr"] = atr(df, period=14)
        
        # Enhanced indicators with proper MACD
        df["ema_short"] = ema(df["close"].astype(float), self.ema_period // 2)  # Shorter EMA
        df["macd"] = df["ema_short"] - df["ema"]  # MACD line
        df["macd_signal"] = df["macd"].rolling(window=9).mean()  # Signal line
        df["macd_histogram"] = df["macd"] - df["macd_signal"]  # Histogram
        df["volume_sma"] = df["volume"].rolling(window=20).mean() if "volume" in df.columns else pd.Series(1, index=df.index)
        
        # Additional indicators for more signals
        df["bb_upper"], df["bb_middle"], df["bb_lower"] = bollinger_bands(df, period=20, std_dev=2.0)
        df["stoch_k"], df["stoch_d"] = stochastic(df, k_period=14, d_period=3)
        
        # Trend analysis
        df["price_above_ema"] = df["close"] > df["ema"]
        df["price_above_ema_short"] = df["close"] > df["ema_short"]
        df["ema_trend"] = df["ema"].diff().rolling(window=5).mean()  # EMA trend direction
        df["rsi_trend"] = df["rsi"].diff().rolling(window=3).mean()  # RSI momentum
        
        # Volatility-adjusted levels
        df["volatility"] = df["atr"] / df["close"] * 100  # ATR as percentage
        df["rsi_buy_adj"] = self.rsi_buy + (df["volatility"] * 0.5)  # Adjust RSI levels based on volatility
        df["rsi_sell_adj"] = self.rsi_sell - (df["volatility"] * 0.5)
        
        # Support/Resistance levels
        df["resistance"] = df["high"].rolling(window=20).max()
        df["support"] = df["low"].rolling(window=20).min()
        range_width = df["resistance"] - df["support"]
        df["price_position"] = self._safe_ratio(
            df["close"] - df["support"], range_width, default=0.5
        )
        
        # Bollinger Band position
        bb_range = df["bb_upper"] - df["bb_lower"]
        df["bb_position"] = self._safe_ratio(
            df["close"] - df["bb_lower"], bb_range, default=0.5
        )
        df["bb_width"] = self._safe_ratio(bb_range, df["bb_middle"], default=0.0) * 100
        
        # ML confirmation
        if self._ml is not None:
            self._attempt_train_ml(df)
            
        return df

    def generate_signal(self, df: pd.DataFrame, higher_tf_df: Optional[pd.DataFrame] = None) -> Signal:
        if df.empty or len(df) < max(self.ema_period, self.rsi_period) + 10:
            logger.debug("Signal HOLD: insufficient data")
            return Signal.HOLD
        
        # Store last signal reasons for outcome tracking
        self._last_signal_direction = None
        self._last_signal_reasons = []

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        
        # NEW: Use enhanced market analysis if available
        if self._market_analyzer:
            try:
                spread = float(last.get("spread", 0.0001)) if "spread" in last else 0.0001
                market_analysis = self._market_analyzer.analyze(df, float(last["close"]), spread)
                
                # Session filtering - only trade in good sessions
                if not self._market_analyzer.is_good_trading_session(market_analysis.session):
                    logger.debug(f"Signal HOLD: Bad trading session ({market_analysis.session})")
                    return Signal.HOLD
                
                # Bad trading time check
                if self._market_analyzer.is_bad_trading_time():
                    logger.debug("Signal HOLD: Bad trading time")
                    return Signal.HOLD
            except Exception as e:
                logger.debug(f"Market analysis failed: {e}")
                market_analysis = None
        else:
            market_analysis = None
        
        # Extract key values
        close = float(last["close"])
        ema_v = float(last["ema"])
        ema_short_v = float(last["ema_short"])
        rsi_v = float(last["rsi"])
        atr_v = float(last["atr"])
        volatility = float(last["volatility"])
        
        # Trend indicators
        price_above_ema = bool(last["price_above_ema"])
        price_above_ema_short = bool(last["price_above_ema_short"])
        ema_trend = float(last["ema_trend"])
        rsi_trend = float(last["rsi_trend"])
        macd_v = float(last["macd"])
        macd_signal_v = float(last["macd_signal"])
        macd_histogram_v = float(last["macd_histogram"])
        
        # Dynamic RSI levels based on volatility
        rsi_buy_adj = float(last["rsi_buy_adj"])
        rsi_sell_adj = float(last["rsi_sell_adj"])
        
        # Support/Resistance
        resistance = float(last["resistance"])
        support = float(last["support"])
        price_position = float(last["price_position"])
        
        # Volume analysis if available
        volume = float(last.get("volume", 0)) if "volume" in last else 0
        volume_sma = float(last.get("volume_sma", 1)) if "volume_sma" in last else 1
        volume_ratio = volume / volume_sma if volume_sma > 0 else 1.0
        
        # Market regime detection - avoid choppy markets
        atr_pct = (atr_v / close) * 100 if close > 0 else 0
        ema_slope_strength = abs(ema_trend) / atr_v if atr_v > 0 else 0
        
        # Dynamic threshold adjustment based on market conditions
        # Lower threshold in trending markets, raise in choppy markets
        dynamic_threshold = self.signal_strength_threshold
        
        # Volatility-based adjustment
        if volatility > 1.0:
            dynamic_threshold *= 0.8  # Lower threshold in high volatility (more opportunities)
        elif volatility < 0.3:
            dynamic_threshold *= 1.2  # Raise threshold in low volatility (require stronger signals)
        
        # Symbol-specific volatility baseline adjustment
        # USDJPY typically has lower volatility than XRPUSD
        symbol_volatility_baseline = self._get_symbol_volatility_baseline()
        if symbol_volatility_baseline:
            volatility_ratio = volatility / symbol_volatility_baseline
            if volatility_ratio < 0.5:
                dynamic_threshold *= 0.7  # Much lower threshold for low-volatility symbols
            elif volatility_ratio > 2.0:
                dynamic_threshold *= 1.3  # Higher threshold for high-volatility symbols
        
        # Market regime-based adjustment
        market_regime = self._detect_market_regime(df)
        if market_regime == "TRENDING_HIGH_VOL":
            dynamic_threshold *= 0.8  # Lower threshold in trending high-volatility markets
        elif market_regime == "TRENDING_LOW_VOL":
            dynamic_threshold *= 0.9  # Slightly lower threshold in trending low-volatility markets
        elif market_regime == "CHOPPY_HIGH_VOL":
            dynamic_threshold *= 1.3  # Higher threshold in choppy high-volatility markets
        elif market_regime == "FLAT":
            dynamic_threshold *= 1.5  # Much higher threshold in flat markets
        elif market_regime == "RANGING":
            dynamic_threshold *= 1.1  # Slightly higher threshold in ranging markets
        
        # Less aggressive choppy market filter (only in extremely flat markets)
        if ema_slope_strength < 0.1 and volatility < 0.3:
            logger.debug(f"Signal HOLD: Extremely choppy market - EMA slope={ema_slope_strength:.2f}, vol={volatility:.2f}%")
            return Signal.HOLD
        
        # Less aggressive volume filter (only extremely low volume)
        if volume > 0 and volume_ratio < 0.1:
            logger.debug(f"Signal HOLD: Extremely low volume - ratio={volume_ratio:.2f}")
            return Signal.HOLD
        
        # Strict BUY conditions - require strong confluence
        buy_score = 0
        buy_reasons = []
        
        # 1. Strong oversold with momentum (primary)
        if rsi_v < rsi_buy_adj and price_above_ema_short:
            buy_score += 3
            buy_reasons.append(f"Oversold RSI={rsi_v:.1f} with momentum")
        
        # 2. EMA crossover with strong RSI confirmation
        if price_above_ema_short and not prev["price_above_ema_short"]:
            if rsi_v < 45 and rsi_trend > 0:
                buy_score += 3
                buy_reasons.append("EMA bullish crossover with RSI support")
        
        # 3. Strong uptrend pullback
        if ema_trend > atr_v * 0.2 and rsi_v < 40 and price_above_ema:
            buy_score += 2
            buy_reasons.append("Strong uptrend pullback")
        
        # 4. MACD bullish with price above EMAs
        if macd_v > macd_signal_v and macd_histogram_v > 0 and price_above_ema:
            if rsi_v < 55:
                buy_score += 2
                buy_reasons.append("MACD bullish with trend alignment")
        
        # 5. Price near support with oversold RSI
        if price_position < 0.15 and rsi_v < 35:
            buy_score += 2
            buy_reasons.append("Near support with oversold RSI")
        
        # 6. RSI extreme oversold (only in strong downtrend reversal)
        if rsi_v < 20 and rsi_trend > 0:
            buy_score += 2
            buy_reasons.append("Extreme RSI oversold reversal")
        
        # 7. Volume confirmation (if available)
        if volume > 0 and volume_ratio > 1.2:
            buy_score += 1
            buy_reasons.append(f"High volume confirmation (ratio={volume_ratio:.2f})")
        
        # 8. Bollinger Band bounce (price near lower band)
        bb_position = float(last.get("bb_position", 0.5))
        if bb_position < 0.1:
            buy_score += 2
            buy_reasons.append(f"BB bounce (position={bb_position:.2f})")
        
        # 9. Stochastic oversold
        stoch_k = float(last.get("stoch_k", 50))
        stoch_d = float(last.get("stoch_d", 50))
        if stoch_k < 20 and stoch_d < 20:
            buy_score += 2
            buy_reasons.append(f"Stochastic oversold (K={stoch_k:.1f}, D={stoch_d:.1f})")
        
        # 10. Breakout above recent high with volume
        if len(df) >= 20:
            recent_high = df['high'].tail(20).max()
            if close > recent_high and volume_ratio > 1.0:
                buy_score += 2
                buy_reasons.append(f"Breakout above 20-period high with volume")
        
        # 11. RSI crossing above 30 from below (momentum shift)
        if rsi_v > 30 and rsi_v < 40 and rsi_trend > 0:
            buy_score += 2
            buy_reasons.append("RSI momentum shift above 30")
        
        # 12. Price above both EMAs with rising EMA slope
        if price_above_ema and price_above_ema_short and ema_trend > 0:
            if rsi_v > 45 and rsi_v < 70:
                buy_score += 1
                buy_reasons.append("Strong trend continuation")
        
        # 13. Bullish engulfing candle pattern
        if close > prev['open'] and prev['close'] < prev['open']:
            if close > prev['open'] and last['open'] < prev['close']:
                buy_score += 2
                buy_reasons.append("Bullish engulfing pattern")
        
        # 14. ATR expansion with price above EMA (volatility breakout)
        if atr_pct > 0.5 and price_above_ema and rsi_v > 50:
            buy_score += 1
            buy_reasons.append(f"Volatility breakout (ATR={atr_pct:.2f}%)")
        
        # 15. Price bouncing off EMA with RSI support
        if close > ema_v * 0.998 and close < ema_v * 1.002:
            if rsi_v > 40 and rsi_v < 60:
                buy_score += 1
                buy_reasons.append("Price bouncing off EMA")
        
        # Strict SELL conditions - require strong confluence
        sell_score = 0
        sell_reasons = []
        
        # 1. Strong overbought with momentum (primary)
        if rsi_v > rsi_sell_adj and not price_above_ema_short:
            sell_score += 3
            sell_reasons.append(f"Overbought RSI={rsi_v:.1f} with momentum")
        
        # 2. EMA crossunder with strong RSI confirmation
        if not price_above_ema_short and prev["price_above_ema_short"]:
            if rsi_v > 55 and rsi_trend < 0:
                sell_score += 3
                sell_reasons.append("EMA bearish crossover with RSI resistance")
        
        # 3. Strong downtrend pullback
        if ema_trend < -atr_v * 0.2 and rsi_v > 60 and not price_above_ema:
            sell_score += 2
            sell_reasons.append("Strong downtrend pullback")
        
        # 4. MACD bearish with price below EMAs
        if macd_v < macd_signal_v and macd_histogram_v < 0 and not price_above_ema:
            if rsi_v > 45:
                sell_score += 2
                sell_reasons.append("MACD bearish with trend alignment")
        
        # 5. Price near resistance with overbought RSI
        if price_position > 0.85 and rsi_v > 65:
            sell_score += 2
            sell_reasons.append("Near resistance with overbought RSI")
        
        # 6. RSI extreme overbought (only in strong uptrend reversal)
        if rsi_v > 80 and rsi_trend < 0:
            sell_score += 2
            sell_reasons.append("Extreme RSI overbought reversal")
        
        # 7. Volume confirmation (if available)
        if volume > 0 and volume_ratio > 1.2:
            sell_score += 1
            sell_reasons.append(f"High volume confirmation (ratio={volume_ratio:.2f})")
        
        # 8. Bollinger Band bounce (price near upper band)
        bb_position = float(last.get("bb_position", 0.5))
        if bb_position > 0.9:
            sell_score += 2
            sell_reasons.append(f"BB bounce (position={bb_position:.2f})")
        
        # 9. Stochastic overbought
        stoch_k = float(last.get("stoch_k", 50))
        stoch_d = float(last.get("stoch_d", 50))
        if stoch_k > 80 and stoch_d > 80:
            sell_score += 2
            sell_reasons.append(f"Stochastic overbought (K={stoch_k:.1f}, D={stoch_d:.1f})")
        
        # 10. Breakout below recent low with volume
        if len(df) >= 20:
            recent_low = df['low'].tail(20).min()
            if close < recent_low and volume_ratio > 1.0:
                sell_score += 2
                sell_reasons.append(f"Breakout below 20-period low with volume")
        
        # 11. RSI crossing below 70 from above (momentum shift)
        if rsi_v < 70 and rsi_v > 60 and rsi_trend < 0:
            sell_score += 2
            sell_reasons.append("RSI momentum shift below 70")
        
        # 12. Price below both EMAs with falling EMA slope
        if not price_above_ema and not price_above_ema_short and ema_trend < 0:
            if rsi_v < 55 and rsi_v > 30:
                sell_score += 1
                sell_reasons.append("Strong downtrend continuation")
        
        # 13. Bearish engulfing candle pattern
        if close < prev['open'] and prev['close'] > prev['open']:
            if close < prev['open'] and last['open'] > prev['close']:
                sell_score += 2
                sell_reasons.append("Bearish engulfing pattern")
        
        # 14. ATR expansion with price below EMA (volatility breakdown)
        if atr_pct > 0.5 and not price_above_ema and rsi_v < 50:
            sell_score += 1
            sell_reasons.append(f"Volatility breakdown (ATR={atr_pct:.2f}%)")
        
        # 15. Price bouncing off EMA from above with RSI resistance
        if close > ema_v * 0.998 and close < ema_v * 1.002:
            if rsi_v < 60 and rsi_v > 40:
                sell_score += 1
                sell_reasons.append("Price bouncing off EMA from above")
        
        # Filter signal reasons based on proven performance with adaptive thresholds
        filtered_buy_reasons = buy_reasons
        filtered_sell_reasons = sell_reasons

        # Recalculate scores based on filtered reasons with ensemble weighting
        # Ensemble: Different indicator types get bonus for diversity
        indicator_types_buy = set()
        for reason in filtered_buy_reasons:
            if "RSI" in reason:
                indicator_types_buy.add("momentum")
            elif "EMA" in reason:
                indicator_types_buy.add("trend")
            elif "MACD" in reason:
                indicator_types_buy.add("momentum")
            elif "BB" in reason:
                indicator_types_buy.add("volatility")
            elif "Stochastic" in reason:
                indicator_types_buy.add("momentum")
            elif "volume" in reason.lower():
                indicator_types_buy.add("volume")
            elif "support" in reason.lower() or "resistance" in reason.lower():
                indicator_types_buy.add("price_level")
        
        indicator_types_sell = set()
        for reason in filtered_sell_reasons:
            if "RSI" in reason:
                indicator_types_sell.add("momentum")
            elif "EMA" in reason:
                indicator_types_sell.add("trend")
            elif "MACD" in reason:
                indicator_types_sell.add("momentum")
            elif "BB" in reason:
                indicator_types_sell.add("volatility")
            elif "Stochastic" in reason:
                indicator_types_sell.add("momentum")
            elif "volume" in reason.lower():
                indicator_types_sell.add("volume")
            elif "support" in reason.lower() or "resistance" in reason.lower():
                indicator_types_sell.add("price_level")
        
        # Ensemble diversity bonus
        diversity_bonus_buy = len(indicator_types_buy) * 0.5  # 0.5 points per unique indicator type
        diversity_bonus_sell = len(indicator_types_sell) * 0.5
        
        filtered_buy_score = self._score_reasons(filtered_buy_reasons) + diversity_bonus_buy
        
        filtered_sell_score = self._score_reasons(filtered_sell_reasons) + diversity_bonus_sell
        
        # Determine signal based on filtered score (using dynamic threshold)
        desired = Signal.HOLD
        # Require minimum of 2 signal reasons to trade (stricter)
        if filtered_buy_score >= dynamic_threshold and filtered_buy_score > filtered_sell_score and len(filtered_buy_reasons) >= 2:
            desired = Signal.BUY
            logger.info(f"BUY signal: {', '.join(filtered_buy_reasons)} | Score={filtered_buy_score}/{dynamic_threshold:.1f}")
            # Track signal reasons for performance analysis
            if self._signal_tracker:
                self._signal_tracker.record_signal("BUY", filtered_buy_reasons)
            # Store for outcome tracking
            self._last_signal_direction = "BUY"
            self._last_signal_reasons = filtered_buy_reasons
        elif filtered_sell_score >= dynamic_threshold and filtered_sell_score > filtered_buy_score and len(filtered_sell_reasons) >= 2:
            desired = Signal.SELL
            logger.info(f"SELL signal: {', '.join(filtered_sell_reasons)} | Score={filtered_sell_score}/{dynamic_threshold:.1f}")
            # Track signal reasons for performance analysis
            if self._signal_tracker:
                self._signal_tracker.record_signal("SELL", filtered_sell_reasons)
            # Store for outcome tracking
            self._last_signal_direction = "SELL"
            self._last_signal_reasons = filtered_sell_reasons
        else:
            logger.debug(f"Signal HOLD: buy_score={filtered_buy_score}, sell_score={filtered_sell_score}, threshold={dynamic_threshold:.1f}, reasons_buy={len(filtered_buy_reasons)}, reasons_sell={len(filtered_sell_reasons)}")
        
        # Additional safety filters (relaxed to increase trade frequency)
        if desired == Signal.BUY:
            # Entry timing: Prefer pullbacks to support/EMA
            # Check if price is pulling back to a good entry level
            close_price = float(last["close"])
            ema_value = float(last["ema"])
            support_level = float(last["support"])
            
            # Calculate distance from support and EMA
            dist_from_support = (close_price - support_level) / atr_v if atr_v > 0 else 0
            dist_from_ema = abs(close_price - ema_value) / atr_v if atr_v > 0 else 0
            
            # Bonus for good entry timing (pullback)
            if dist_from_support < 1.0 or dist_from_ema < 0.5:
                logger.debug(f"BUY entry timing bonus: pullback detected (dist_support={dist_from_support:.2f}, dist_ema={dist_from_ema:.2f})")
                # Already scored, just logging
            
            # Don't buy if extremely close to resistance (relaxed from 0.85 to 0.95)
            if price_position > 0.95:
                logger.debug(f"BUY rejected: too close to resistance (position={price_position:.2f})")
                desired = Signal.HOLD
            # Don't buy if EMA trend is extremely bearish (relaxed from 0.3 to 0.5)
            if ema_trend < -atr_v * 0.5:
                logger.debug(f"BUY rejected: extreme bearish EMA trend (ema_trend={ema_trend:.6f})")
                desired = Signal.HOLD
                
        elif desired == Signal.SELL:
            # Entry timing: Prefer pullbacks to resistance/EMA
            # Check if price is pulling back to a good entry level
            close_price = float(last["close"])
            ema_value = float(last["ema"])
            resistance_level = float(last["resistance"])
            
            # Calculate distance from resistance and EMA
            dist_from_resistance = (resistance_level - close_price) / atr_v if atr_v > 0 else 0
            dist_from_ema = abs(close_price - ema_value) / atr_v if atr_v > 0 else 0
            
            # Bonus for good entry timing (pullback)
            if dist_from_resistance < 1.0 or dist_from_ema < 0.5:
                logger.debug(f"SELL entry timing bonus: pullback detected (dist_resistance={dist_from_resistance:.2f}, dist_ema={dist_from_ema:.2f})")
                # Already scored, just logging
            
            # Don't sell if extremely close to support (relaxed from 0.15 to 0.05)
            if price_position < 0.05:
                logger.debug(f"SELL rejected: too close to support (position={price_position:.2f})")
                desired = Signal.HOLD
            # Don't sell if EMA trend is extremely bullish (relaxed from 0.3 to 0.5)
            if ema_trend > atr_v * 0.5:
                logger.debug(f"SELL rejected: extreme bullish EMA trend (ema_trend={ema_trend:.6f})")
                desired = Signal.HOLD
        
        # Multi-timeframe confirmation (if enabled and data available)
        # Allow neutral higher timeframe to pass (less strict)
        if self.use_multi_timeframe_confirmation and desired != Signal.HOLD and higher_tf_df is not None:
            higher_signal = self.check_higher_timeframe_signal(higher_tf_df)
            if higher_signal != desired and higher_signal != Signal.HOLD:
                logger.debug(f"Multi-timeframe rejection: local={desired.value}, higher={higher_signal.value}")
                desired = Signal.HOLD
            else:
                logger.info(f"Multi-timeframe confirmed: {desired.value} (higher={higher_signal.value})")
        
        # Signal consistency check - require 1 consecutive signal (less strict)
        if desired != self._last_signal:
            self._signal_count = 1
        else:
            self._signal_count += 1
        self._last_signal = desired
        
        # ML confirmation if enabled
        if desired != Signal.HOLD and self._ml is not None:
            ml_confirm = self._ml.confirm(df, desired)
            if ml_confirm is False:
                desired = Signal.HOLD
                logger.debug("ML confirmation rejected signal")
            elif ml_confirm is True:
                logger.debug("ML confirmation approved signal")
        
        # NEW: Trade scoring system if available
        if desired != Signal.HOLD and self._trade_scorer and market_analysis:
            try:
                higher_tf_trend = None
                if higher_tf_df is not None and len(higher_tf_df) > 0:
                    higher_tf_last = higher_tf_df.iloc[-1]
                    higher_tf_price = float(higher_tf_last["close"])
                    higher_tf_ema = float(higher_tf_last.get("ema", higher_tf_price))
                    if higher_tf_price > higher_tf_ema:
                        higher_tf_trend = "bullish"
                    else:
                        higher_tf_trend = "bearish"
                
                signal_reasons = filtered_buy_reasons if desired == Signal.BUY else (filtered_sell_reasons if desired == Signal.SELL else [])
                
                # Dynamic trade score threshold based on symbol volatility
                dynamic_min_score = self._trade_scorer.min_score
                symbol_baseline = self._get_symbol_volatility_baseline()
                if symbol_baseline:
                    volatility_ratio = volatility / symbol_baseline
                    if volatility_ratio < 0.5:
                        dynamic_min_score *= 0.8  # Lower score threshold for low-volatility symbols
                    elif volatility_ratio > 2.0:
                        dynamic_min_score *= 1.2  # Higher score threshold for high-volatility symbols
                
                trade_score = self._trade_scorer.score_trade(
                    direction=desired.value,
                    market_analysis=market_analysis,
                    df=df,
                    higher_tf_trend=higher_tf_trend,
                    spread_threshold=0.0002,
                    signal_reasons=signal_reasons,
                    min_score=dynamic_min_score,  # Use dynamic threshold
                )
                
                if not trade_score.passed:
                    logger.info(f"Trade score rejected: {trade_score.score_percentage:.1f}% (min {dynamic_min_score:.0f})")
                    desired = Signal.HOLD
                else:
                    logger.info(f"Trade score passed: {trade_score.score_percentage:.1f}% - {', '.join(trade_score.reasons)}")
            except Exception as e:
                logger.debug(f"Trade scoring failed: {e}")
        
        return desired

