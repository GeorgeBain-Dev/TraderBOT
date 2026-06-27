"""
Multi-Timeframe Analysis
Analyzes market across multiple timeframes for higher quality signals
H4 → overall trend
H1 → setup
M5/M15 → precise entry
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

import pandas as pd
import numpy as np

try:
    from market_analysis import TrendDirection, MarketPhase
except ImportError:
    from .market_analysis import TrendDirection, MarketPhase


class TimeframeSignal(Enum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    NEUTRAL = "neutral"
    SELL = "sell"
    STRONG_SELL = "strong_sell"


@dataclass
class TimeframeAnalysis:
    """Analysis result for a single timeframe"""
    timeframe: str
    signal: TimeframeSignal
    trend: TrendDirection
    phase: MarketPhase
    strength: float  # 0-100
    confidence: float  # 0-100


@dataclass
class MultiTimeframeSignal:
    """Combined multi-timeframe analysis result"""
    h4: TimeframeAnalysis
    h1: TimeframeAnalysis
    m15: Optional[TimeframeAnalysis]
    m5: Optional[TimeframeAnalysis]
    overall_signal: TimeframeSignal
    overall_confidence: float
    confluence_score: float  # 0-100
    is_aligned: bool  # Are all timeframes aligned?
    entry_timeframe: str  # Which timeframe to use for entry


class MultiTimeframeAnalyzer:
    """
    Analyzes market across multiple timeframes for higher quality signals.
    
    H4 → overall trend
    H1 → setup
    M5/M15 → precise entry
    """
    
    def __init__(self):
        self.timeframe_weights = {
            'H4': 0.4,  # 40% weight for overall trend
            'H1': 0.35,  # 35% weight for setup
            'M15': 0.15,  # 15% weight for entry
            'M5': 0.1,  # 10% weight for precise entry
        }
    
    def analyze(
        self,
        h4_df: pd.DataFrame,
        h1_df: pd.DataFrame,
        m15_df: Optional[pd.DataFrame] = None,
        m5_df: Optional[pd.DataFrame] = None,
    ) -> MultiTimeframeSignal:
        """
        Perform multi-timeframe analysis
        
        Args:
            h4_df: H4 timeframe data
            h1_df: H1 timeframe data
            m15_df: M15 timeframe data (optional)
            m5_df: M5 timeframe data (optional)
        
        Returns:
            MultiTimeframeSignal with comprehensive analysis
        """
        # Analyze each timeframe
        h4_analysis = self._analyze_timeframe(h4_df, 'H4')
        h1_analysis = self._analyze_timeframe(h1_df, 'H1')
        m15_analysis = self._analyze_timeframe(m15_df, 'M15') if m15_df is not None else None
        m5_analysis = self._analyze_timeframe(m5_df, 'M5') if m5_df is not None else None
        
        # Calculate overall signal
        overall_signal, overall_confidence = self._calculate_overall_signal(
            h4_analysis, h1_analysis, m15_analysis, m5_analysis
        )
        
        # Calculate confluence score
        confluence_score = self._calculate_confluence_score(
            h4_analysis, h1_analysis, m15_analysis, m5_analysis
        )
        
        # Check if timeframes are aligned
        is_aligned = self._check_alignment(h4_analysis, h1_analysis, m15_analysis, m5_analysis)
        
        # Determine entry timeframe
        entry_timeframe = self._determine_entry_timeframe(
            m5_analysis, m15_analysis, h1_analysis
        )
        
        return MultiTimeframeSignal(
            h4=h4_analysis,
            h1=h1_analysis,
            m15=m15_analysis,
            m5=m5_analysis,
            overall_signal=overall_signal,
            overall_confidence=overall_confidence,
            confluence_score=confluence_score,
            is_aligned=is_aligned,
            entry_timeframe=entry_timeframe,
        )
    
    def _analyze_timeframe(self, df: pd.DataFrame, timeframe: str) -> TimeframeAnalysis:
        """Analyze a single timeframe"""
        if df.empty or len(df) < 20:
            return TimeframeAnalysis(
                timeframe=timeframe,
                signal=TimeframeSignal.NEUTRAL,
                trend=TrendDirection.NEUTRAL,
                phase=MarketPhase.CONSOLIDATION,
                strength=0.0,
                confidence=0.0,
            )
        
        last = df.iloc[-1]
        
        # Calculate indicators
        ema_short = df['close'].ewm(span=20).mean().iloc[-1]
        ema_long = df['close'].ewm(span=50).mean().iloc[-1]
        price = float(last['close'])
        
        # Determine trend
        if price > ema_short > ema_long:
            trend = TrendDirection.STRONG_BULLISH
        elif price > ema_short:
            trend = TrendDirection.BULLISH
        elif price < ema_short < ema_long:
            trend = TrendDirection.STRONG_BEARISH
        elif price < ema_short:
            trend = TrendDirection.BEARISH
        else:
            trend = TrendDirection.NEUTRAL
        
        # Determine signal based on trend
        if trend == TrendDirection.STRONG_BULLISH:
            signal = TimeframeSignal.STRONG_BUY
        elif trend == TrendDirection.BULLISH:
            signal = TimeframeSignal.BUY
        elif trend == TrendDirection.STRONG_BEARISH:
            signal = TimeframeSignal.STRONG_SELL
        elif trend == TrendDirection.BEARISH:
            signal = TimeframeSignal.SELL
        else:
            signal = TimeframeSignal.NEUTRAL
        
        # Calculate strength (based on EMA separation)
        ema_separation = abs(ema_short - ema_long) / price * 100
        strength = min(ema_separation * 10, 100)
        
        # Calculate confidence (based on recent price action)
        if len(df) >= 10:
            recent_change = abs(df['close'].iloc[-1] - df['close'].iloc[-10]) / df['close'].iloc[-10] * 100
            confidence = min(recent_change * 5, 100)
        else:
            confidence = 50.0
        
        # Determine market phase
        high = df['high'].tail(20).max()
        low = df['low'].tail(20).min()
        range_pct = (high - low) / price * 100
        
        if range_pct < 0.5:
            phase = MarketPhase.CONSOLIDATION
        elif trend == TrendDirection.NEUTRAL:
            phase = MarketPhase.RANGING
        else:
            phase = MarketPhase.TRENDING
        
        return TimeframeAnalysis(
            timeframe=timeframe,
            signal=signal,
            trend=trend,
            phase=phase,
            strength=strength,
            confidence=confidence,
        )
    
    def _calculate_overall_signal(
        self,
        h4: TimeframeAnalysis,
        h1: TimeframeAnalysis,
        m15: Optional[TimeframeAnalysis],
        m5: Optional[TimeframeAnalysis],
    ) -> Tuple[TimeframeSignal, float]:
        """Calculate overall signal from all timeframes"""
        signals = [h4.signal, h1.signal]
        if m15:
            signals.append(m15.signal)
        if m5:
            signals.append(m5.signal)
        
        # Count signal types
        buy_count = sum(1 for s in signals if s in [TimeframeSignal.BUY, TimeframeSignal.STRONG_BUY])
        sell_count = sum(1 for s in signals if s in [TimeframeSignal.SELL, TimeframeSignal.STRONG_SELL])
        strong_buy_count = sum(1 for s in signals if s == TimeframeSignal.STRONG_BUY)
        strong_sell_count = sum(1 for s in signals if s == TimeframeSignal.STRONG_SELL)
        
        # Determine overall signal
        if strong_buy_count >= 2:
            return TimeframeSignal.STRONG_BUY, 90.0
        elif strong_sell_count >= 2:
            return TimeframeSignal.STRONG_SELL, 90.0
        elif buy_count >= len(signals) * 0.6:
            return TimeframeSignal.BUY, 75.0
        elif sell_count >= len(signals) * 0.6:
            return TimeframeSignal.SELL, 75.0
        else:
            return TimeframeSignal.NEUTRAL, 50.0
    
    def _calculate_confluence_score(
        self,
        h4: TimeframeAnalysis,
        h1: TimeframeAnalysis,
        m15: Optional[TimeframeAnalysis],
        m5: Optional[TimeframeAnalysis],
    ) -> float:
        """Calculate confluence score (0-100) based on timeframe alignment"""
        analyses = [h4, h1]
        if m15:
            analyses.append(m15)
        if m5:
            analyses.append(m5)
        
        if len(analyses) < 2:
            return 50.0
        
        # Check if all timeframes agree on direction
        bullish_count = sum(1 for a in analyses if a.signal in [TimeframeSignal.BUY, TimeframeSignal.STRONG_BUY])
        bearish_count = sum(1 for a in analyses if a.signal in [TimeframeSignal.SELL, TimeframeSignal.STRONG_SELL])
        
        total = len(analyses)
        
        # High confluence: 75%+ agreement
        if bullish_count / total >= 0.75 or bearish_count / total >= 0.75:
            return 90.0
        # Medium confluence: 50-75% agreement
        elif bullish_count / total >= 0.5 or bearish_count / total >= 0.5:
            return 70.0
        # Low confluence: <50% agreement
        else:
            return 40.0
    
    def _check_alignment(
        self,
        h4: TimeframeAnalysis,
        h1: TimeframeAnalysis,
        m15: Optional[TimeframeAnalysis],
        m5: Optional[TimeframeAnalysis],
    ) -> bool:
        """Check if all timeframes are aligned in the same direction"""
        analyses = [h4, h1]
        if m15:
            analyses.append(m15)
        if m5:
            analyses.append(m5)
        
        if len(analyses) < 2:
            return False
        
        # Check if all agree on bullish or bearish
        all_bullish = all(a.signal in [TimeframeSignal.BUY, TimeframeSignal.STRONG_BUY] for a in analyses)
        all_bearish = all(a.signal in [TimeframeSignal.SELL, TimeframeSignal.STRONG_SELL] for a in analyses)
        
        return all_bullish or all_bearish
    
    def _determine_entry_timeframe(
        self,
        m5: Optional[TimeframeAnalysis],
        m15: Optional[TimeframeAnalysis],
        h1: TimeframeAnalysis,
    ) -> str:
        """Determine which timeframe to use for entry"""
        # Prefer M5 if available and has good signal
        if m5 and m5.signal in [TimeframeSignal.BUY, TimeframeSignal.SELL]:
            if m5.strength > 50:
                return 'M5'
        
        # Fall back to M15
        if m15 and m15.signal in [TimeframeSignal.BUY, TimeframeSignal.SELL]:
            if m15.strength > 50:
                return 'M15'
        
        # Fall back to H1
        return 'H1'
    
    def get_entry_signal(self, mtf_signal: MultiTimeframeSignal) -> Optional[str]:
        """
        Get the entry signal based on multi-timeframe analysis
        
        Returns:
            'BUY', 'SELL', or None if no valid signal
        """
        # Only trade if timeframes are aligned
        if not mtf_signal.is_aligned:
            return None
        
        # Only trade if confluence is high
        if mtf_signal.confluence_score < 70:
            return None
        
        # Only trade if overall confidence is high
        if mtf_signal.overall_confidence < 70:
            return None
        
        # Return the overall signal
        if mtf_signal.overall_signal == TimeframeSignal.STRONG_BUY:
            return 'BUY'
        elif mtf_signal.overall_signal == TimeframeSignal.BUY:
            return 'BUY'
        elif mtf_signal.overall_signal == TimeframeSignal.STRONG_SELL:
            return 'SELL'
        elif mtf_signal.overall_signal == TimeframeSignal.SELL:
            return 'SELL'
        else:
            return None
