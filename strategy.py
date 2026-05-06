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
    
    # Timeframe-specific parameters
    timeframe_params: Dict[str, Dict] = None
    
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
        df["price_position"] = (df["close"] - df["support"]) / (df["resistance"] - df["support"])  # 0-1 position in range
        
        # ML confirmation
        if self._ml is not None:
            self._ml.fit(df)
            
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if df.empty or len(df) < max(self.ema_period, self.rsi_period) + 10:
            return Signal.HOLD

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        
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
        
        # Initialize signal
        desired = Signal.HOLD
        signal_strength = 0
        
        # Enhanced BUY conditions with proper MACD
        buy_conditions = [
            # Strong oversold condition
            (rsi_v < rsi_buy_adj, 3),
            # RSI low and above EMA (momentum)
            (rsi_v < (rsi_buy_adj + 10) and price_above_ema, 2),
            # Price near support with low RSI
            (price_position < 0.2 and rsi_v < 45, 2),
            # EMA crossover with RSI confirmation
            (price_above_ema_short and not prev["price_above_ema_short"] and rsi_v < 50, 2),
            # Strong uptrend with pullback
            (ema_trend > 0 and rsi_trend > 0 and rsi_v < 40, 1),
            # MACD bullish crossover with signal line
            (macd_v > macd_signal_v and macd_histogram_v > 0, 2),
            # MACD bullish with RSI support
            (macd_v > 0 and rsi_v < 45 and price_above_ema, 1)
        ]
        
        # Enhanced SELL conditions with proper MACD
        sell_conditions = [
            # Strong overbought condition
            (rsi_v > rsi_sell_adj, 3),
            # RSI high and below EMA (momentum down)
            (rsi_v > (rsi_sell_adj - 10) and not price_above_ema, 2),
            # Price near resistance with high RSI
            (price_position > 0.8 and rsi_v > 55, 2),
            # EMA crossunder with RSI confirmation
            (not price_above_ema_short and prev["price_above_ema_short"] and rsi_v > 50, 2),
            # Strong downtrend with pullback
            (ema_trend < 0 and rsi_trend < 0 and rsi_v > 60, 1),
            # MACD bearish crossover with signal line
            (macd_v < macd_signal_v and macd_histogram_v < 0, 2),
            # MACD bearish with RSI resistance
            (macd_v < 0 and rsi_v > 55 and not price_above_ema, 1)
        ]
        
        # Calculate signal strength
        for condition, strength in buy_conditions:
            if condition:
                desired = Signal.BUY
                signal_strength += strength
                
        for condition, strength in sell_conditions:
            if condition:
                desired = Signal.SELL
                signal_strength += strength
        
        # Apply minimum signal strength threshold (reduced to 0.8 for more trades)
        if signal_strength < 0.8:
            desired = Signal.HOLD
        
        # Risk management filters
        if desired == Signal.BUY:
            # Don't buy if too close to resistance or in strong downtrend
            if price_position > 0.9 or ema_trend < -atr_v * 0.1:
                desired = Signal.HOLD
                
        elif desired == Signal.SELL:
            # Don't sell if too close to support or in strong uptrend
            if price_position < 0.1 or ema_trend > atr_v * 0.1:
                desired = Signal.HOLD
        
        # Signal consistency check (avoid whipsaws)
        if desired != self._last_signal:
            self._signal_count = 1
        else:
            self._signal_count += 1
            
        # Require signal confirmation (reduced to 1 consecutive signal for more activity)
        if self._signal_count < 1 and desired != Signal.HOLD:
            desired = Signal.HOLD
        else:
            self._last_signal = desired
        
        # ML confirmation if enabled
        if desired != Signal.HOLD and self._ml is not None:
            ml_confirm = self._ml.confirm(df, desired)
            if ml_confirm is False:
                desired = Signal.HOLD
            elif ml_confirm is True:
                # Boost confidence if ML agrees
                pass
        
        # Log decision for debugging
        if desired != Signal.HOLD:
            logger.debug(f"{self.current_timeframe} {desired}: RSI={rsi_v:.1f}, EMA={ema_v:.5f}, "
                        f"Position={price_position:.2f}, Strength={signal_strength}")
        
        return desired

