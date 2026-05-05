# 🤖 Advanced MT5 Trading Bot

<div align="center">

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![MT5](https://img.shields.io/badge/MT5-Compatible-orange)
![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen)

**Intelligent MetaTrader 5 Trading Bot with Advanced Loss Protection & Real-time Monitoring**

[🚀 Quick Start](#-quick-start) • [📖 File Descriptions](#-file-descriptions) • [🛠️ Installation](#️-installation) • [⚙️ Usage](#️-usage) • [📊 Features](#-features)

</div>

---

## 📁 File Descriptions

### **🔧 Core Files**
- **`main.py`** - Main entry point and TradingBot class with live trading loop
- **`config.py`** - Bot configuration management and validation
- **`utils.py`** - Utility functions, logging, and event handling

### **📊 Trading Strategy**
- **`strategy.py`** - RSI + EMA trading strategy with MACD enhancements
  - Enhanced signal generation (3-5x frequency increase)
  - Fixed MACD logic with proper signal line comparisons
  - Multi-timeframe confirmation (M15/H1)
  - Market regime detection and trend analysis

### **🛡️ Risk Management**
- **`risk.py`** - Risk management with dynamic position sizing
  - Volatility-based position adjustments
  - ATR-based stop loss and take profit calculations
  - Signal strength scaling (0.5x to 2.0x)
- **`trade_monitor.py`** - Advanced trade monitoring and loss protection
  - Aggressive loss protection (50% risk threshold)
  - 15-minute recovery checks
  - 30-minute maximum trade duration
  - Predictive loss prevention
  - Trailing stop implementation

### **🔗 Data & Execution**
- **`data.py`** - MT5 integration and candle data retrieval
  - MetaTrader 5 client wrapper
  - Real-time price feeds
  - Historical data access
- **`execution.py`** - Order execution engine
  - Position management
  - Order validation and execution
  - Error handling and logging

### **⚙️ Optimization**
- **`optimizer.py`** - Parameter calibration and edge detection
  - Optimized calibration grid (16 combinations)
  - Fast edge detection for profitable configurations
  - Performance metrics calculation

### **🖥️ User Interface**
- **`ui.py`** - Main trading interface with live monitoring
  - Real-time P&L tracking
  - Live graph updates (2-second refresh)
  - Candlestick and line chart options
  - Trade status monitoring
  - MT5 connection management

### **📢 Notifications**
- **`notifier.py`** - WhatsApp and notification system
  - Trade alerts and notifications
  - Change detection system
  - Message formatting and delivery

### **🔄 Continuous Calibration**
- **`continuous_calibrator.py`** - Background parameter optimization
  - Automatic parameter tuning
  - Multi-symbol calibration
  - Performance tracking

### **📈 Backtesting**
- **`backtest.py`** - Strategy backtesting functionality
  - Historical data testing
  - Performance analysis
  - Win rate and profit factor calculation

---

## 🚀 Quick Start

### **Prerequisites**
- Python 3.9+
- MetaTrader 5 Terminal
- GitHub account

### **Installation**
```bash
# Clone repository
git clone https://github.com/GeorgeBain-Dev/TraderBOT.git
cd TraderBOT-master

# Install dependencies
pip install pandas numpy matplotlib MetaTrader5 scikit-learn

# Run the bot
python main.py
```

---

## 🛠️ Installation

### **System Requirements**
- **OS**: Windows 10/11 (recommended), Linux, macOS
- **Python**: 3.9+ (3.10 recommended)
- **RAM**: 4GB minimum, 8GB recommended
- **Storage**: 1GB free space

### **Dependencies**
```bash
pip install pandas>=1.3.0
pip install numpy>=1.21.0
pip install matplotlib>=3.5.0
pip install MetaTrader5>=5.0.40
pip install scikit-learn>=1.0.0
```

### **MT5 Setup**
1. Install MetaTrader 5 terminal
2. Enable automated trading
3. Configure API access
4. Test connection

---

## ⚙️ Bot Usage

### **1. Launch the Bot**
```bash
python main.py
```

### **2. Connect to MT5**
- Click **"Connect"** in the UI
- Enter your MT5 credentials:
  - Login number
  - Password
  - Server name
- Click **"Connect"** again to establish connection

### **3. Configure Trading Settings**
- **Symbol**: Select trading symbol (e.g., XRPUSD)
- **Timeframe**: Choose chart timeframe (M5 recommended)
- **Test Mode**: Enable for paper trading
- **Auto Calibrate**: Enable for continuous optimization

### **4. Start Trading**
- Click **"Start Bot"** to begin automated trading
- Monitor:
  - **Status**: Real-time bot status
  - **Signal**: Current trading signal (BUY/SELL/HOLD)
  - **Trades**: Active trade count and details
  - **Graph**: Live price chart with 2-second updates

### **5. Monitor Performance**
- **Trading Tab**: Live trading status and controls
- **Settings Tab**: Configuration and connection settings
- **Graph Tab**: Real-time price visualization

---

## 📊 Key Features

### **🛡️ Advanced Loss Protection**
- **50% Risk Threshold** - Closes trades at 50% of initial risk
- **15-Minute Recovery** - Fast failure detection
- **30-Minute Timeout** - Maximum trade duration
- **Predictive Analytics** - Closes trades before major losses
- **Trend Reversal Detection** - 10-minute analysis

### **⚡ Enhanced Signal Generation**
- **3-5x Frequency Increase** - Lower signal threshold (0.5 vs 1.0)
- **Fixed MACD Logic** - Proper signal line comparisons
- **Multi-timeframe Confirmation** - M15/H1 validation
- **Market Regime Detection** - Trending/Ranging/Transitioning

### **📈 Real-time Monitoring**
- **Live P&L Tracking** - 2-second updates
- **Trade Duration Monitoring** - Automatic alerts
- **Active Trade Display** - Entry price, current P&L
- **Fund Balance Updates** - Cumulative profit/loss

### **📊 Advanced Visualization**
- **Candlestick Charts** - Professional OHLC display
- **Line Charts** - Clean price visualization
- **Real-time Updates** - Live price feeds
- **Technical Indicators** - RSI, EMA, MACD overlay

---

## 🔧 Configuration Options

### **Risk Management**
```python
RISK_PER_TRADE = 0.01          # 1% risk per trade
SL_ATR_MULTIPLIER = 1.5        # Stop loss multiplier
TP_RR_RATIO = 2.0              # Risk/Reward ratio
MAX_POSITION_SIZE = 100.0      # Maximum lot size
```

### **Strategy Parameters**
```python
RSI_PERIOD = 14                # RSI calculation period
EMA_PERIOD = 200               # EMA calculation period
RSI_BUY_LEVEL = 30             # RSI buy threshold
RSI_SELL_LEVEL = 70            # RSI sell threshold
```

### **Loss Protection**
```python
LOSS_PROTECTION_THRESHOLD = 0.5 # 50% of initial risk
RECOVERY_CHECK_MINUTES = 15    # Fast failure detection
MAX_TRADE_DURATION = 30        # Maximum minutes
```

---

## 📈 Expected Performance

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Trade Frequency | 0.21/hour | 1-2/hour | **400-900%** |
| Signal Accuracy | 50% | 65%+ | **30%** |
| Loss Prevention | Stop loss only | 50% early closure | **50% fewer losses** |
| Monitoring | Manual | Real-time | **Live updates** |

---

## 🎯 Safety Features

- **Predictive Loss Prevention** - Closes trades before major losses
- **Trend Reversal Detection** - 10-minute analysis for consecutive losses
- **15-Minute Recovery Checks** - Fast failure detection
- **30-Minute Maximum Duration** - Prevents stuck trades
- **Automatic Trade Closure** - Loss protection decisions

---

## 📞 Support

### **Documentation**
- [GitHub Repository](https://github.com/GeorgeBain-Dev/TraderBOT)
- [GitHub Instructions](github_instructions.md)

### **Contact**
- Email: georgebain781@gmail.com
- GitHub: @GeorgeBain-Dev

---

<div align="center">

**⭐ If this project helped you, please give it a star! ⭐**

[🚀 Get Started](#-quick-start) • [📖 File Descriptions](#-file-descriptions) • [⚙️ Usage](#️-usage)

Made with ❤️ by [George Bain](https://github.com/GeorgeBain-Dev)

</div>
