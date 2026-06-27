"""
Market Analysis Layer
Analyzes trend, volatility, momentum, and market structure
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple

import pandas as pd
import numpy as np


class TrendDirection(Enum):
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"


class MarketPhase(Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    CONSOLIDATION = "consolidation"
    VOLATILE = "volatile"


@dataclass
class MarketAnalysis:
    """Comprehensive market analysis result"""
    trend: TrendDirection
    phase: MarketPhase
    volatility: float  # Current ATR
    volatility_percent: float  # ATR as % of price
    momentum: float  # RSI-based momentum
    trend_strength: float  # 0-100 trend strength
    support: float
    resistance: float
    price_position: float  # 0-1 position in range
    spread: float
    spread_pips: float
    session: str
    is_high_volatility: bool
    is_low_volatility: bool
    is_liquidity_zone: bool


class MarketAnalyzer:
    """Analyzes market conditions for trading decisions"""
    
    def __init__(self):
        self.session_times = {
            'asian': {'start': 0, 'end': 8},  # 00:00-08:00 UTC
            'london': {'start': 7, 'end': 16},  # 07:00-16:00 UTC
            'new_york': {'start': 12, 'end': 21},  # 12:00-21:00 UTC
            'overlap': {'start': 12, 'end': 16},  # London/NY overlap
        }
    
    def analyze(self, df: pd.DataFrame, current_price: float, spread: float) -> MarketAnalysis:
        """Perform comprehensive market analysis"""
        if df.empty or len(df) < 50:
            return self._default_analysis(current_price, spread)
        
        last = df.iloc[-1]
        
        # Trend analysis using EMAs
        ema_50 = last.get('ema_50', last.get('ema', 0))
        ema_200 = last.get('ema_200', 0)
        price = float(last['close'])
        
        # Determine trend direction
        trend = self._determine_trend(price, ema_50, ema_200, df)
        
        # Market phase
        phase = self._determine_phase(df, trend)
        
        # Volatility
        atr = float(last.get('atr', 0))
        volatility_percent = (atr / price * 100) if price > 0 else 0
        
        # Momentum (RSI-based)
        rsi = float(last.get('rsi', 50))
        momentum = (rsi - 50) / 50  # -1 to 1
        
        # Trend strength
        trend_strength = self._calculate_trend_strength(df, trend)
        
        # Support/Resistance
        support, resistance = self._calculate_support_resistance(df)
        price_position = self._calculate_price_position(price, support, resistance)
        
        # Session
        session = self._get_current_session()
        
        # Volatility classification
        is_high_volatility = volatility_percent > 1.0
        is_low_volatility = volatility_percent < 0.3
        
        # Liquidity zone detection
        is_liquidity_zone = self._is_liquidity_zone(price, support, resistance)
        
        return MarketAnalysis(
            trend=trend,
            phase=phase,
            volatility=atr,
            volatility_percent=volatility_percent,
            momentum=momentum,
            trend_strength=trend_strength,
            support=support,
            resistance=resistance,
            price_position=price_position,
            spread=spread,
            spread_pips=spread * 10000,  # Approximate pips
            session=session,
            is_high_volatility=is_high_volatility,
            is_low_volatility=is_low_volatility,
            is_liquidity_zone=is_liquidity_zone,
        )
    
    def _determine_trend(self, price: float, ema_50: float, ema_200: float, df: pd.DataFrame) -> TrendDirection:
        """Determine trend direction using EMAs"""
        if ema_200 == 0:
            return TrendDirection.NEUTRAL
        
        # Price position relative to EMAs
        above_ema50 = price > ema_50
        above_ema200 = price > ema_200
        
        # EMA slope
        if len(df) >= 10:
            ema_slope = df['ema'].iloc[-1] - df['ema'].iloc[-10]
        else:
            ema_slope = 0
        
        if above_ema200 and above_ema50 and ema_slope > 0:
            return TrendDirection.STRONG_BULLISH
        elif above_ema200 and above_ema50:
            return TrendDirection.BULLISH
        elif not above_ema200 and not above_ema50 and ema_slope < 0:
            return TrendDirection.STRONG_BEARISH
        elif not above_ema200 and not above_ema50:
            return TrendDirection.BEARISH
        else:
            return TrendDirection.NEUTRAL
    
    def _determine_phase(self, df: pd.DataFrame, trend: TrendDirection) -> MarketPhase:
        """Determine market phase (trending, ranging, consolidation)"""
        if len(df) < 20:
            return MarketPhase.CONSOLIDATION
        
        # Calculate price range
        high = df['high'].tail(20).max()
        low = df['low'].tail(20).min()
        range_pct = (high - low) / df['close'].iloc[-1] * 100
        
        # ATR comparison
        atr = df['atr'].iloc[-1]
        avg_atr = df['atr'].tail(20).mean()
        
        if range_pct < 0.5 and atr < avg_atr * 0.7:
            return MarketPhase.CONSOLIDATION
        elif trend in [TrendDirection.NEUTRAL]:
            return MarketPhase.RANGING
        elif atr > avg_atr * 1.5:
            return MarketPhase.VOLATILE
        else:
            return MarketPhase.TRENDING
    
    def _calculate_trend_strength(self, df: pd.DataFrame, trend: TrendDirection) -> float:
        """Calculate trend strength (0-100)"""
        if len(df) < 20:
            return 50.0
        
        # ADX-like calculation using price movement
        high_low_range = df['high'].tail(20).max() - df['low'].tail(20).min()
        price_change = abs(df['close'].iloc[-1] - df['close'].iloc[-20])
        
        if high_low_range == 0:
            return 50.0
        
        strength = (price_change / high_low_range) * 100
        return min(max(strength, 0), 100)
    
    def _calculate_support_resistance(self, df: pd.DataFrame) -> Tuple[float, float]:
        """Calculate support and resistance levels"""
        if len(df) < 20:
            return 0.0, 0.0
        
        # Simple support/resistance using recent highs/lows
        resistance = df['high'].tail(20).max()
        support = df['low'].tail(20).min()
        
        return support, resistance
    
    def _calculate_price_position(self, price: float, support: float, resistance: float) -> float:
        """Calculate price position in range (0-1)"""
        if resistance == support:
            return 0.5
        
        position = (price - support) / (resistance - support)
        return min(max(position, 0), 1)
    
    def _get_current_session(self) -> str:
        """Determine current trading session"""
        now = datetime.now(timezone.utc)
        hour = now.hour
        
        # Check sessions
        for session_name, times in self.session_times.items():
            if times['start'] <= hour < times['end']:
                return session_name
        
        return 'off_hours'
    
    def _is_liquidity_zone(self, price: float, support: float, resistance: float) -> bool:
        """Check if price is in a liquidity zone"""
        if resistance == support:
            return False
        
        # Near support or resistance (within 10% of range)
        range_size = resistance - support
        distance_to_support = abs(price - support)
        distance_to_resistance = abs(price - resistance)
        
        return distance_to_support < range_size * 0.1 or distance_to_resistance < range_size * 0.1
    
    def _default_analysis(self, current_price: float, spread: float) -> MarketAnalysis:
        """Return default analysis when insufficient data"""
        return MarketAnalysis(
            trend=TrendDirection.NEUTRAL,
            phase=MarketPhase.CONSOLIDATION,
            volatility=0.0,
            volatility_percent=0.0,
            momentum=0.0,
            trend_strength=50.0,
            support=0.0,
            resistance=0.0,
            price_position=0.5,
            spread=spread,
            spread_pips=spread * 10000,
            session=self._get_current_session(),
            is_high_volatility=False,
            is_low_volatility=True,
            is_liquidity_zone=False,
        )
    
    def is_good_trading_session(self, session: str) -> bool:
        """Check if current session is good for trading"""
        # Allow all sessions except off-hours to increase trade frequency
        return session != 'off_hours'
    
    def is_bad_trading_time(self) -> bool:
        """Check if current time is bad for trading"""
        session = self._get_current_session()
        # Only block off-hours to increase trade frequency
        return session == 'off_hours'
