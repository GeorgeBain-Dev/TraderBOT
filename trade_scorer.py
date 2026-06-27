"""
Trade Scoring System
Scores potential trades based on multiple factors
Only trades with score >= threshold are executed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

try:
    from market_analysis import MarketAnalysis, TrendDirection, MarketPhase
except ImportError:
    from .market_analysis import MarketAnalysis, TrendDirection, MarketPhase


@dataclass
class TradeScore:
    """Trade score with detailed breakdown"""
    total_score: float
    max_score: float
    score_percentage: float
    breakdown: Dict[str, float]
    passed: bool
    reasons: List[str]


class TradeScorer:
    """
    Scores potential trades based on multiple factors.
    Only trades with score >= threshold are executed.
    
    Scoring System (max 100 points):
    - Trend Alignment: 30 points
    - Multi-Timeframe Confirmation: 20 points
    - Candlestick Confirmation: 15 points
    - Volume Confirmation: 10 points
    - Session Quality: 10 points
    - Spread Quality: 5 points
    - Support/Resistance Alignment: 10 points
    - Signal Performance Bonus: +20 points (dynamic)
    """
    
    def __init__(self, min_score: float = 70.0, signal_tracker=None):
        self.min_score = min_score
        self.signal_tracker = signal_tracker
        self.weights = {
            'trend_alignment': 30,
            'multi_timeframe': 20,
            'candlestick': 15,
            'volume': 10,
            'session': 10,
            'spread': 5,
            'support_resistance': 10,
            'signal_performance': 20,  # Bonus for proven signals
        }
        self.max_score = sum(self.weights.values())
    
    def score_trade(
        self,
        direction: str,
        market_analysis: MarketAnalysis,
        df: pd.DataFrame,
        higher_tf_trend: Optional[str] = None,
        spread_threshold: float = 0.0002,  # 2 pips
        signal_reasons: Optional[List[str]] = None,
        min_score: Optional[float] = None,
    ) -> TradeScore:
        """
        Score a potential trade
        
        Args:
            direction: 'BUY' or 'SELL'
            market_analysis: Market analysis result
            df: Current timeframe data
            higher_tf_trend: Trend from higher timeframe ('bullish', 'bearish', 'neutral')
            spread_threshold: Maximum acceptable spread
            signal_reasons: List of signal reasons for performance weighting
        
        Returns:
            TradeScore with detailed breakdown
        """
        scores = {}
        reasons = []
        
        # 1. Trend Alignment (30 points)
        trend_score = self._score_trend_alignment(direction, market_analysis.trend)
        scores['trend_alignment'] = trend_score
        if trend_score > 20:
            reasons.append(f"Strong trend alignment ({market_analysis.trend.value})")
        
        # 2. Multi-Timeframe Confirmation (20 points)
        mtf_score = self._score_multi_timeframe(direction, higher_tf_trend)
        scores['multi_timeframe'] = mtf_score
        if mtf_score > 15:
            reasons.append(f"Multi-timeframe confirmed ({higher_tf_trend})")
        
        # 3. Candlestick Confirmation (15 points)
        candle_score = self._score_candlestick(direction, df)
        scores['candlestick'] = candle_score
        if candle_score > 10:
            reasons.append("Strong candlestick confirmation")
        
        # 4. Volume Confirmation (10 points)
        volume_score = self._score_volume(df)
        scores['volume'] = volume_score
        if volume_score > 7:
            reasons.append("Volume confirmation")
        
        # 5. Session Quality (10 points)
        session_score = self._score_session(market_analysis.session)
        scores['session'] = session_score
        if session_score > 7:
            reasons.append(f"Good session ({market_analysis.session})")
        
        # 6. Spread Quality (5 points)
        spread_score = self._score_spread(market_analysis.spread, spread_threshold)
        scores['spread'] = spread_score
        if spread_score > 3:
            reasons.append("Good spread")
        
        # 7. Support/Resistance Alignment (10 points)
        sr_score = self._score_support_resistance(direction, market_analysis)
        scores['support_resistance'] = sr_score
        if sr_score > 7:
            reasons.append("Support/Resistance alignment")
        
        # 8. Signal Performance Bonus (20 points) - NEW
        signal_perf_score = self._score_signal_performance(signal_reasons or [])
        scores['signal_performance'] = signal_perf_score
        if signal_perf_score > 15:
            reasons.append("Proven signal performance")
        
        total_score = sum(scores.values())
        score_percentage = (total_score / self.max_score) * 100
        
        # Use dynamic min_score if provided, otherwise use default
        effective_min_score = min_score if min_score is not None else self.min_score
        passed = total_score >= effective_min_score
        
        return TradeScore(
            total_score=total_score,
            max_score=self.max_score,
            score_percentage=score_percentage,
            breakdown=scores,
            passed=passed,
            reasons=reasons,
        )
    
    def _score_trend_alignment(self, direction: str, trend: TrendDirection) -> float:
        """Score trend alignment (max 30 points)"""
        if direction == 'BUY':
            if trend == TrendDirection.STRONG_BULLISH:
                return 30.0
            elif trend == TrendDirection.BULLISH:
                return 25.0
            elif trend == TrendDirection.NEUTRAL:
                return 10.0
            else:  # Bearish
                return 0.0
        else:  # SELL
            if trend == TrendDirection.STRONG_BEARISH:
                return 30.0
            elif trend == TrendDirection.BEARISH:
                return 25.0
            elif trend == TrendDirection.NEUTRAL:
                return 10.0
            else:  # Bullish
                return 0.0
    
    def _score_multi_timeframe(self, direction: str, higher_tf_trend: Optional[str]) -> float:
        """Score multi-timeframe confirmation (max 20 points)"""
        if higher_tf_trend is None:
            return 5.0  # No higher timeframe data
        
        if direction == 'BUY':
            if higher_tf_trend == 'bullish':
                return 20.0
            elif higher_tf_trend == 'neutral':
                return 10.0
            else:  # bearish
                return 0.0
        else:  # SELL
            if higher_tf_trend == 'bearish':
                return 20.0
            elif higher_tf_trend == 'neutral':
                return 10.0
            else:  # bullish
                return 0.0
    
    def _score_candlestick(self, direction: str, df: pd.DataFrame) -> float:
        """Score candlestick confirmation (max 15 points)"""
        if df.empty or len(df) < 3:
            return 5.0
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        open_price = float(last['open'])
        close_price = float(last['close'])
        high_price = float(last['high'])
        low_price = float(last['low'])
        
        prev_open = float(prev['open'])
        prev_close = float(prev['close'])
        
        # Calculate candle body and wick
        body = abs(close_price - open_price)
        range_size = high_price - low_price
        body_ratio = body / range_size if range_size > 0 else 0
        
        # Bullish candle patterns
        if direction == 'BUY':
            # Bullish engulfing
            if close_price > open_price and prev_close < prev_open and close_price > prev_open and open_price < prev_close:
                return 15.0
            # Strong bullish candle (body > 60% of range)
            elif close_price > open_price and body_ratio > 0.6:
                return 12.0
            # Simple bullish candle
            elif close_price > open_price:
                return 8.0
            else:
                return 3.0
        
        # Bearish candle patterns
        else:  # SELL
            # Bearish engulfing
            if close_price < open_price and prev_close > prev_open and close_price < prev_open and open_price > prev_close:
                return 15.0
            # Strong bearish candle (body > 60% of range)
            elif close_price < open_price and body_ratio > 0.6:
                return 12.0
            # Simple bearish candle
            elif close_price < open_price:
                return 8.0
            else:
                return 3.0
    
    def _score_volume(self, df: pd.DataFrame) -> float:
        """Score volume confirmation (max 10 points)"""
        if df.empty or len(df) < 20:
            return 5.0
        
        last = df.iloc[-1]
        
        # Check if volume data exists
        if 'volume' not in last:
            return 5.0
        
        current_volume = float(last['volume'])
        
        # Calculate average volume
        avg_volume = df['volume'].tail(20).mean()
        
        if avg_volume == 0:
            return 5.0
        
        volume_ratio = current_volume / avg_volume
        
        # High volume spike
        if volume_ratio > 1.5:
            return 10.0
        # Above average volume
        elif volume_ratio > 1.2:
            return 8.0
        # Normal volume
        elif volume_ratio > 0.8:
            return 6.0
        # Low volume
        else:
            return 3.0
    
    def _score_session(self, session: str) -> float:
        """Score session quality (max 10 points)"""
        # Best sessions: London, NY, overlap
        if session in ['london', 'new_york', 'overlap']:
            return 10.0
        # Acceptable: Asian (sometimes)
        elif session == 'asian':
            return 5.0
        # Bad: off-hours
        else:
            return 2.0
    
    def _score_spread(self, spread: float, threshold: float) -> float:
        """Score spread quality (max 5 points)"""
        if spread <= threshold * 0.5:  # Very tight spread
            return 5.0
        elif spread <= threshold:  # Acceptable spread
            return 4.0
        elif spread <= threshold * 1.5:  # Slightly wide
            return 2.0
        else:  # Too wide
            return 0.0
    
    def _score_support_resistance(self, direction: str, analysis: MarketAnalysis) -> float:
        """Score support/resistance alignment (max 10 points)"""
        price_position = analysis.price_position
        
        if direction == 'BUY':
            # Buy near support
            if price_position < 0.2:
                return 10.0
            elif price_position < 0.4:
                return 7.0
            elif price_position < 0.6:
                return 4.0
            else:  # Near resistance
                return 0.0
        else:  # SELL
            # Sell near resistance
            if price_position > 0.8:
                return 10.0
            elif price_position > 0.6:
                return 7.0
            elif price_position > 0.4:
                return 4.0
            else:  # Near support
                return 0.0
    
    def _score_signal_performance(self, signal_reasons: List[str]) -> float:
        """Score signal performance based on historical data (max 20 points)"""
        if not self.signal_tracker or not signal_reasons:
            return 10.0  # Neutral score if no tracker or reasons
        
        proven_count = 0
        total_reasons = len(signal_reasons)
        
        for reason in signal_reasons:
            if self.signal_tracker.is_signal_proven(reason, min_trades=5, min_win_rate=40.0, min_profit_factor=1.05):
                proven_count += 1
        
        if total_reasons == 0:
            return 10.0
        
        proven_ratio = proven_count / total_reasons
        
        # Score based on ratio of proven signals
        if proven_ratio >= 0.8:
            return 20.0  # Most signals are proven
        elif proven_ratio >= 0.6:
            return 15.0  # Majority proven
        elif proven_ratio >= 0.4:
            return 10.0  # Mixed
        elif proven_ratio >= 0.2:
            return 5.0  # Few proven
        else:
            return 0.0  # No proven signals
    
    def set_min_score(self, score: float) -> None:
        """Update minimum score threshold"""
        self.min_score = score
