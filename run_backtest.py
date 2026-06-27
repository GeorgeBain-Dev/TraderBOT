#!/usr/bin/env python3
"""
Automated Backtest Script
Tests strategy improvements with default parameters
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

try:
    from data import MT5Client, get_historical_data
    from strategy import RsiEmaStrategy
    from risk import RiskManager
    from backtest import compare_strategies, print_comparison
except ImportError:
    print("Error: Could not import required modules. Run from the TraderBOT directory.")
    sys.exit(1)


def main():
    print("=" * 80)
    print("AUTOMATED STRATEGY BACKTEST")
    print("=" * 80)
    
    # Default test parameters
    symbol = "USDJPY"
    timeframe = "M5"
    days_back = 30
    
    print(f"\nTesting on {symbol} {timeframe} for past {days_back} days...")
    print("This may take a moment...\n")
    
    # Initialize MT5
    client = MT5Client()
    if not client.initialize():
        print("Error: Could not initialize MT5. Make sure MT5 terminal is running.")
        sys.exit(1)
    
    try:
        # Fetch historical data
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days_back)
        
        print(f"Fetching data from {start_time.date()} to {end_time.date()}...")
        df = get_historical_data(client, symbol, timeframe, start_time, end_time)
        
        if df.empty:
            print("Error: No data received. Check symbol and timeframe.")
            sys.exit(1)
        
        print(f"Received {len(df)} candles.\n")
        
        # Create strategies to compare
        strategies = {}
        
        # VERY LOW (baseline - should generate many signals)
        very_low_strategy = RsiEmaStrategy(
            use_ml_confirmation=False,
            signal_strength_threshold=0.5,  # Extremely low
            use_multi_timeframe_confirmation=False,  # Disabled
        )
        very_low_strategy.update_timeframe(timeframe)
        very_low_risk = RiskManager(risk_per_trade=0.01, sl_atr_mult=1.5, tp_rr=2.0)
        strategies["VERY LOW (threshold=0.5, no MTF)"] = (very_low_strategy, very_low_risk)
        
        # LOW (conservative improvement)
        low_strategy = RsiEmaStrategy(
            use_ml_confirmation=False,
            signal_strength_threshold=1.5,  # Low
            use_multi_timeframe_confirmation=False,  # Disabled
        )
        low_strategy.update_timeframe(timeframe)
        low_risk = RiskManager(risk_per_trade=0.01, sl_atr_mult=1.5, tp_rr=2.0)
        strategies["LOW (threshold=1.5, no MTF)"] = (low_strategy, low_risk)
        
        # MEDIUM (balanced with MTF)
        medium_strategy = RsiEmaStrategy(
            use_ml_confirmation=False,
            signal_strength_threshold=2.0,  # Medium
            use_multi_timeframe_confirmation=True,  # Enabled
        )
        medium_strategy.update_timeframe(timeframe)
        medium_risk = RiskManager(risk_per_trade=0.01, sl_atr_mult=1.5, tp_rr=2.0)
        strategies["MEDIUM (threshold=2.0, with MTF)"] = (medium_strategy, medium_risk)
        
        # HIGH (strict with MTF)
        high_strategy = RsiEmaStrategy(
            use_ml_confirmation=False,
            signal_strength_threshold=2.5,  # High
            use_multi_timeframe_confirmation=True,  # Enabled
        )
        high_strategy.update_timeframe(timeframe)
        high_risk = RiskManager(risk_per_trade=0.01, sl_atr_mult=1.5, tp_rr=2.5)
        strategies["HIGH (threshold=2.5, with MTF)"] = (high_strategy, high_risk)
        
        # Run comparison
        print("Running backtests...")
        results = compare_strategies(df, strategies, initial_balance=10_000.0)
        
        # Print results
        print_comparison(results)
        
        # Recommendation
        print("\n" + "=" * 80)
        print("RECOMMENDATION")
        print("=" * 80)
        
        best_strategy = max(results.items(), key=lambda x: x[1].total_pnl)
        print(f"\nBest Total P&L: {best_strategy[0]} (${best_strategy[1].total_pnl:.2f})")
        
        best_winrate = max(results.items(), key=lambda x: x[1].win_rate)
        print(f"Best Win Rate: {best_winrate[0]} ({best_winrate[1].win_rate:.1%})")
        
        best_pf = max(results.items(), key=lambda x: x[1].profit_factor if x[1].profit_factor != float('inf') else 0)
        print(f"Best Profit Factor: {best_pf[0]} ({best_pf[1].profit_factor:.2f})")
        
        print("\nSuggested next steps:")
        print("1. Review the detailed results above")
        print("2. Enable test_mode=True in config for paper trading")
        print("3. Run bot live for 1-2 weeks in test mode")
        print("4. Compare paper trading results with backtest")
        print("5. If results match, consider small live test with 0.25-0.5% risk")
        
    finally:
        client.shutdown()
        print("\nMT5 connection closed.")


if __name__ == "__main__":
    main()
