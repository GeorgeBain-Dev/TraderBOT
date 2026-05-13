from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

# Add matplotlib for graph functionality
try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import pandas as pd
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    plt = None
    Figure = None
    FigureCanvasTkAgg = None
    NavigationToolbar2Tk = None

try:
    from .config import BotConfig
    from .data import get_latest_candles, MT5Client, MT5Error
    from .execution import ExecutionEngine, ExecutionResult
    from .main import TradingBot
    from .notifier import WhatsAppConfig, WhatsAppNotifier
    from .trade_monitor import TradeMonitor
    from .continuous_calibrator import ContinuousCalibrator
    from .utils import BotEvent, setup_logging
except ImportError:
    # Fallback for direct execution
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    from config import BotConfig
    from data import get_latest_candles, MT5Client, MT5Error
    from execution import ExecutionEngine, ExecutionResult
    from main import TradingBot
    from notifier import WhatsAppConfig, WhatsAppNotifier
    from trade_monitor import TradeMonitor
    from continuous_calibrator import ContinuousCalibrator
    from utils import BotEvent, setup_logging

logger = setup_logging()


class BotApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MT5 Trading Bot")
        self.geometry("1020x650")
        self.minsize(920, 580)
        self._init_style()

        self.event_q: "queue.Queue[BotEvent]" = queue.Queue()
        self.bot: Optional[TradingBot] = None
        self.client = MT5Client()
        self.is_logged_in = False
        self.execution_engine = ExecutionEngine(self.client)
        self.trade_monitor = TradeMonitor(self.client, self.execution_engine)
        self.continuous_calibrator: Optional[ContinuousCalibrator] = None

        self.symbol_var = tk.StringVar(value="XRPUSD")
        self.timeframe_var = tk.StringVar(value="M5")
        self.mt5_path_var = tk.StringVar(value="")
        self.mt5_login_var = tk.StringVar(value="")
        self.mt5_pass_var = tk.StringVar(value="")
        self.mt5_server_var = tk.StringVar(value="")
        self.connected_var = tk.StringVar(value="Disconnected")
        self.whatsapp_enabled_var = tk.BooleanVar(value=False)
        self.whatsapp_to_var = tk.StringVar(value="")
        self.running_var = tk.StringVar(value="Stopped")
        self.mode_var = tk.StringVar(value="IDLE")
        self.price_var = tk.StringVar(value="—")
        self.bidask_var = tk.StringVar(value="—")
        self.signal_var = tk.StringVar(value="HOLD")
        self.err_var = tk.StringVar(value="")
        self.funds_var = tk.StringVar(value="—")
        self.trades_var = tk.StringVar(value="0")
        self.trades_detail_var = tk.StringVar(value="")
        self.test_mode_var = tk.BooleanVar(value=False)
        self.auto_calibrate_var = tk.BooleanVar(value=False)
        self._logo_img = None
        self.symbol_combo = None  # Reference to symbol combobox

        self._build_ui()
        self.after(200, self._poll_events)
        
        # Load market watch symbols after UI is built
        self.after(500, self._load_market_watch_symbols)
        
        # Start trade monitoring loop
        self.after(1000, self._monitor_trades)

    def _load_market_watch_symbols(self) -> None:
        """Load symbols from the account's market watch"""
        try:
            # Try to initialize MT5 and get symbols
            if self.client.initialize():
                self.is_logged_in = True
                self.connected_var.set("Connected")
                
                # Get all available symbols
                symbols = self.client.symbols_get()
                if symbols:
                    # Extract symbol names and sort them
                    symbol_names = [sym.name for sym in symbols if sym.name and sym.visible]
                    symbol_names.sort()
                    
                    # Update the combobox values
                    if self.symbol_combo:
                        self.symbol_combo['values'] = symbol_names[:30]  # Limit to first 30 for performance
                        logger.info(f"Loaded {len(symbol_names)} symbols from market watch")
                        
                        # Set first symbol as default if current is not in list
                        if self.symbol_var.get() not in symbol_names:
                            self.symbol_var.set(symbol_names[0] if symbol_names else "XRPUSD")
                else:
                    logger.warning("No symbols found in market watch")
                    # Fallback to default symbols
                    self._set_default_symbols()
            else:
                # MT5 not available - use default symbols
                logger.warning("MT5 not initialized - using default symbols")
                self._set_default_symbols()
                
        except Exception as e:
            logger.error(f"Failed to load market watch symbols: {e}")
            self._set_default_symbols()
    
    def _set_default_symbols(self) -> None:
        """Set default symbol list when market watch is unavailable"""
        default_symbols = ["XRPUSD", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US30", "BTCUSD", "ETHUSD"]
        try:
            if self.symbol_combo:
                self.symbol_combo['values'] = default_symbols
                logger.info("Using default symbol list")
        except Exception as e:
            logger.error(f"Failed to set default symbols: {e}")
    
    def _monitor_trades(self) -> None:
        """Monitor open trades and make management decisions"""
        try:
            if self.trade_monitor.open_trades:
                # Update all trades with current data
                decisions = self.trade_monitor.update_all_trades()
                
                # Execute decisions
                for order_id, decision in decisions:
                    if decision.action in ["CLOSE", "MODIFY_SL"]:
                        success = self.trade_monitor.execute_decision(order_id, decision)
                        if success:
                            logger.info(f"Executed {decision.action} for trade {order_id}: {decision.reason}")
                        else:
                            logger.error(f"Failed to execute {decision.action} for trade {order_id}")
                
                # Update UI with trade summary
                summary = self.trade_monitor.get_trade_summary()
                self.trades_var.set(str(summary['total_trades']))
                
                # Update trades detail
                if summary['trades']:
                    details = []
                    for trade in summary['trades']:
                        details.append(f"{trade['type']} {trade['symbol']} R{trade['unrealized_pnl']:.2f}")
                    self.trades_detail_var.set(" | ".join(details[:3]))  # Show first 3
                else:
                    self.trades_detail_var.set("")
                
                # Update live graph every 2 seconds
                self._refresh_graph()
            
            # Schedule next monitoring cycle (every 2 seconds for live updates)
            self.after(2000, self._monitor_trades)
            
        except Exception as e:
            logger.error(f"Error in trade monitoring: {e}")
            # Continue monitoring even if there's an error
            self.after(10000, self._monitor_trades)

    def _init_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Modern dark theme with better contrast
        self._bg = "#0a0a0f"
        self._panel = "#1a1a2e"
        self._card_bg = "#16213e"
        self._accent = "#e94560"
        self._success = "#00d4aa"
        self._warning = "#f39c12"
        self._fg = "#f5f5f5"
        self._muted = "#8892b0"
        self._border = "#233554"

        self.configure(bg=self._bg)
        style.configure(".", background=self._bg, foreground=self._fg, fieldbackground=self._panel)
        style.configure("TFrame", background=self._bg)
        style.configure("Top.TFrame", background=self._panel)
        style.configure("Card.TFrame", background=self._card_bg)
        
        # Modern fonts
        style.configure("TLabel", background=self._bg, foreground=self._fg, font=("Inter", 10))
        style.configure("Title.TLabel", background=self._panel, foreground=self._accent, font=("Inter", 18, "bold"))
        style.configure("SubTitle.TLabel", background=self._panel, foreground=self._muted, font=("Inter", 9))
        style.configure("Card.TLabel", background=self._card_bg, foreground=self._fg, font=("Inter", 11, "bold"))
        style.configure("Value.TLabel", background=self._card_bg, foreground=self._success, font=("Inter", 12, "bold"))
        style.configure("TSeparator", background=self._border)

        # Modern buttons with better styling
        style.configure("TButton", padding=(12, 8), background=self._card_bg, foreground=self._fg, 
                       borderwidth=1, relief="flat", font=("Inter", 10, "bold"))
        style.map("TButton", background=[("active", self._accent)], foreground=[("active", "#ffffff")])
        
        style.configure("Start.TButton", background=self._success, foreground="#000000", 
                       font=("Inter", 10, "bold"))
        style.map("Start.TButton", background=[("active", "#00b894")])
        
        style.configure("Stop.TButton", background=self._accent, foreground="#ffffff", 
                       font=("Inter", 10, "bold"))
        style.map("Stop.TButton", background=[("active", "#d63031")])

        # Modern card styling
        style.configure("Card.TLabelframe", background=self._card_bg, foreground=self._fg, 
                       bordercolor=self._border, relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=self._card_bg, foreground=self._accent, 
                       font=("Inter", 10, "bold"))

        # Modern inputs
        style.configure("TCombobox", fieldbackground=self._panel, background=self._panel, 
                       foreground=self._fg, bordercolor=self._border, relief="flat")
        style.configure("TEntry", fieldbackground=self._panel, background=self._panel, 
                       foreground=self._fg, bordercolor=self._border, relief="flat")
        style.configure("TCheckbutton", background=self._bg, foreground=self._fg, font=("Inter", 10))

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self, padding=12, style="Top.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(2, weight=1)

        # Logo slot (bot/assets/logo.png optional)
        logo = ttk.Frame(top, style="Top.TFrame")
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        self._logo_label = ttk.Label(logo, text="🤖 BOT", style="Title.TLabel")
        self._logo_label.grid(row=0, column=0, sticky="w")
        ttk.Label(logo, text="MT5 Auto Trader", style="SubTitle.TLabel").grid(row=1, column=0, sticky="w")
        self._load_logo_if_exists()

        # Primary controls
        ctrls = ttk.Frame(top, style="Top.TFrame")
        ctrls.grid(row=0, column=1, sticky="w")

        ttk.Label(ctrls, text="Symbol", style="SubTitle.TLabel").grid(row=0, column=0, padx=(0, 6))
        ttk.Combobox(
            ctrls,
            textvariable=self.symbol_var,
            values=["XRPUSD", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US30"],
            width=10,
            state="normal",
        ).grid(row=0, column=1, padx=(0, 14))
        self.symbol_combo = ctrls.winfo_children()[-1]  # Store reference

        ttk.Label(ctrls, text="Timeframe", style="SubTitle.TLabel").grid(row=0, column=2, padx=(0, 6))
        ttk.Combobox(
            ctrls,
            textvariable=self.timeframe_var,
            values=["M1", "M5", "M15", "M30", "H1", "H4", "D1"],
            width=6,
            state="readonly",
        ).grid(row=0, column=3, padx=(0, 14))

        ttk.Button(ctrls, text="⚙️ Settings", command=self._open_menu).grid(row=0, column=4, padx=(0, 10))
        ttk.Label(ctrls, textvariable=self.connected_var, style="SubTitle.TLabel").grid(row=0, column=5, padx=(0, 10))

        self.start_btn = ttk.Button(ctrls, text="▶️ Start", command=self._start, state="disabled", style="Start.TButton")
        self.start_btn.grid(row=0, column=6, padx=(0, 8))
        self.stop_btn = ttk.Button(ctrls, text="⏹️ Stop", command=self._stop, state="disabled", style="Stop.TButton")
        self.stop_btn.grid(row=0, column=7, padx=(0, 0))

        # Error line
        ttk.Label(top, textvariable=self.err_var, style="SubTitle.TLabel").grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(8, 0)
        )

        sep = ttk.Separator(self, orient="horizontal")
        sep.grid(row=1, column=0, sticky="ew")

        status = ttk.Frame(self, padding=10)
        status.grid(row=2, column=0, sticky="nsew")
        status.columnconfigure(0, weight=1)
        status.rowconfigure(2, weight=1)

        cards = ttk.Frame(status, style="Card.TFrame")
        cards.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for i in range(6):
            cards.columnconfigure(i, weight=1)

        self._modern_card(cards, 0, "🤖 Bot Status", self.running_var)
        self._modern_card(cards, 1, "📊 Mode", self.mode_var)
        self._modern_card(cards, 2, "💰 Price", self.price_var)
        self._modern_card(cards, 3, "📈 Bid/Ask", self.bidask_var)
        self._modern_card(cards, 4, "🎯 Signal", self.signal_var)
        self._modern_card(cards, 5, "💳 Funds", self.funds_var)
        # Active trades is shown in the detail panel

        detail = ttk.Labelframe(status, text="📋 Active Trades", padding=8, style="Card.TLabelframe")
        detail.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        detail.columnconfigure(0, weight=1)
        ttk.Label(detail, textvariable=self.trades_detail_var, font=("Inter", 10)).grid(row=0, column=0, sticky="w")

        # Create notebook for tabs
        self.notebook = ttk.Notebook(status)
        self.notebook.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        
        # Trade Log tab
        log_frame = ttk.Frame(self.notebook)
        self.notebook.add(log_frame, text="📝 Trade Log")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log = tk.Text(
            log_frame,
            height=12,
            wrap="word",
            state="disabled",
            bg=self._panel,
            fg=self._fg,
            insertbackground=self._fg,
            selectbackground=self._accent,
            font=("Consolas", 9),
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        
        # Graph tab
        self._build_graph_tab()

    def _build_graph_tab(self) -> None:
        """Build the graph tab with price chart"""
        if not MATPLOTLIB_AVAILABLE:
            # Show placeholder if matplotlib not available
            graph_frame = ttk.Frame(self.notebook)
            self.notebook.add(graph_frame, text="📊 Graph")
            
            placeholder = ttk.Label(
                graph_frame, 
                text="📊 Graph\n\nMatplotlib not installed.\nInstall with: pip install matplotlib",
                style="SubTitle.TLabel",
                justify="center"
            )
            placeholder.pack(expand=True, pady=50)
            return
            
        # Create graph frame
        graph_frame = ttk.Frame(self.notebook)
        self.notebook.add(graph_frame, text="📊 Graph")
        graph_frame.columnconfigure(0, weight=1)
        graph_frame.rowconfigure(1, weight=1)
        
        # Graph controls
        controls = ttk.Frame(graph_frame, style="Card.TFrame")
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        ttk.Label(controls, text="📊 Price Chart", style="Title.TLabel").pack(side="left", padx=(10, 20))
        ttk.Button(controls, text="🔄 Refresh", command=self._refresh_graph).pack(side="left", padx=(0, 10))
        
        self.graph_status = ttk.Label(controls, text="No data available", style="SubTitle.TLabel")
        self.graph_status.pack(side="left", padx=(10, 0))
        
        # Create matplotlib figure
        self.fig = Figure(figsize=(12, 6), facecolor=self._card_bg)
        self.ax = self.fig.add_subplot(111, facecolor=self._panel)
        
        # Style the plot
        self.ax.set_facecolor(self._panel)
        self.fig.patch.set_facecolor(self._card_bg)
        self.ax.tick_params(colors=self._fg)
        self.ax.spines['bottom'].set_color(self._fg)
        self.ax.spines['left'].set_color(self._fg)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.xaxis.label.set_color(self._fg)
        self.ax.yaxis.label.set_color(self._fg)
        self.ax.title.set_color(self._fg)
        
        # Create canvas
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")
        
        # Add toolbar
        toolbar_frame = ttk.Frame(graph_frame, style="Card.TFrame")
        toolbar_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.update()
        
        # Initial empty plot
        self._update_graph_placeholder()
        
    def _update_graph_placeholder(self) -> None:
        """Update graph with placeholder message"""
        if not MATPLOTLIB_AVAILABLE:
            return
            
        self.ax.clear()
        self.ax.text(0.5, 0.5, 'Connect to MT5 and start bot\nto see price chart', 
                    horizontalalignment='center', verticalalignment='center',
                    transform=self.ax.transAxes, fontsize=12, color=self._muted)
        self.ax.set_title(f"{self.symbol_var.get()} - {self.timeframe_var.get()}", color=self._fg)
        self.canvas.draw()
        
    def _refresh_graph(self) -> None:
        """Refresh the graph with latest data"""
        if not MATPLOTLIB_AVAILABLE or not self.is_logged_in:
            self._update_graph_placeholder()
            return
            
        try:
            # Get latest candle data
            df = get_latest_candles(self.client, self.symbol_var.get(), self.timeframe_var.get(), n=100)
            
            if df is not None and not df.empty:
                self._update_graph(df)
                self.graph_status.config(text=f"Updated: {len(df)} candles")
            else:
                self._update_graph_placeholder()
                self.graph_status.config(text="No data available")
                
        except Exception as e:
            logger.error(f"Error refreshing graph: {e}")
            self.graph_status.config(text=f"Error: {str(e)}")
            
    def _update_graph(self, df: pd.DataFrame) -> None:
        """Update graph with candle data"""
        if not MATPLOTLIB_AVAILABLE:
            return
            
        self.ax.clear()
        
        # Plot candlestick chart (simplified - just close price line)
        self.ax.plot(df.index, df['close'], color=self._success, linewidth=2, label='Close Price')
        
        # Style the plot
        self.ax.set_facecolor(self._panel)
        self.ax.tick_params(colors=self._fg)
        self.ax.spines['bottom'].set_color(self._fg)
        self.ax.spines['left'].set_color(self._fg)
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.xaxis.label.set_color(self._fg)
        self.ax.yaxis.label.set_color(self._fg)
        self.ax.title.set_color(self._fg)
        
        self.ax.set_title(f"{self.symbol_var.get()} - {self.timeframe_var.get()}", color=self._fg)
        self.ax.set_xlabel('Time', color=self._fg)
        self.ax.set_ylabel('Price', color=self._fg)
        self.ax.legend()
        self.ax.grid(True, alpha=0.3, color=self._muted)
        
        # Format x-axis for better readability
        import matplotlib.dates as mdates
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        self.ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        
        self.fig.tight_layout()
        self.canvas.draw()

    def _modern_card(self, parent: ttk.Frame, col: int, title: str, var: tk.StringVar) -> None:
        frame = ttk.Labelframe(parent, text=title, padding=10, style="Card.TLabelframe")
        frame.grid(row=0, column=col, sticky="ew", padx=(0, 8) if col < 5 else 0)
        lbl = ttk.Label(frame, textvariable=var, style="Value.TLabel")
        lbl.grid(row=0, column=0, sticky="w")

    def _card(self, parent: ttk.Frame, col: int, title: str, var: tk.StringVar) -> None:
        frame = ttk.Labelframe(parent, text=title, padding=8, style="Card.TLabelframe")
        frame.grid(row=0, column=col, sticky="ew", padx=(0, 10) if col < 3 else 0)
        lbl = ttk.Label(frame, textvariable=var, font=("Segoe UI", 12))
        lbl.grid(row=0, column=0, sticky="w")

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                evt = self.event_q.get_nowait()
                ts = evt.ts.strftime("%Y-%m-%d %H:%M:%S")
                self._append_log(f"{ts} | {evt.level} | {evt.message}")
        except queue.Empty:
            pass

        if self.bot is not None:
            st = self.bot.status
            self.running_var.set("Running" if st.running else "Stopped")
            self.mode_var.set(getattr(st, "mode", "IDLE") or "IDLE")
            self.signal_var.set(st.last_signal or "HOLD")
            if getattr(st, "mid", 0.0):
                self.price_var.set(f"{float(getattr(st, 'mid', 0.0)):.5f}")
            b = float(getattr(st, "bid", 0.0) or 0.0)
            a = float(getattr(st, "ask", 0.0) or 0.0)
            if b and a:
                self.bidask_var.set(f"{b:.5f} / {a:.5f}")
            else:
                self.bidask_var.set("—")
            if st.balance or st.equity or st.free_margin:
                self.funds_var.set(f"{st.balance:.2f} / {st.equity:.2f} / {st.free_margin:.2f}")
            self.trades_var.set(str(getattr(st, "positions_count", 0) or 0))
            self.trades_detail_var.set(getattr(st, "positions_summary", "") or "—")
            self.err_var.set(st.last_error or "")

        self.after(250, self._poll_events)

    def _load_logo_if_exists(self) -> None:
        try:
            base = os.path.dirname(__file__)
            path = os.path.join(base, "assets", "logo.png")
            if not os.path.exists(path):
                return
            img = tk.PhotoImage(file=path)
            self._logo_img = img
            self._logo_label.configure(image=img, text="")
        except Exception:
            self._logo_img = None

    def _open_menu(self) -> None:
        m = tk.Menu(
            self,
            tearoff=0,
            bg=self._panel,
            fg=self._fg,
            activebackground="#1b1b26",
            activeforeground=self._fg,
        )
        m.add_command(label="Connection settings…", command=self._open_connection_dialog)
        m.add_command(label="WhatsApp settings…", command=self._open_whatsapp_dialog)
        m.add_separator()
        m.add_command(label="Exit", command=self.destroy)
        try:
            m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _open_connection_dialog(self) -> None:
        win = tk.Toplevel(self)
        win.title("MT5 Connection")
        win.configure(bg=self._bg)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frm, text="MT5 path (optional)").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 6))
        ttk.Entry(frm, textvariable=self.mt5_path_var, width=48).grid(row=0, column=1, sticky="w", pady=(0, 6))

        ttk.Label(frm, text="Login").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 6))
        ttk.Entry(frm, textvariable=self.mt5_login_var, width=24).grid(row=1, column=1, sticky="w", pady=(0, 6))

        ttk.Label(frm, text="Password").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(0, 6))
        ttk.Entry(frm, textvariable=self.mt5_pass_var, width=24, show="*").grid(row=2, column=1, sticky="w", pady=(0, 6))

        ttk.Label(frm, text="Server").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=(0, 12))
        ttk.Entry(frm, textvariable=self.mt5_server_var, width=30).grid(row=3, column=1, sticky="w", pady=(0, 12))

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="Connect", command=lambda: (self._connect(), win.destroy())).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="Close", command=win.destroy).grid(row=0, column=1)

    def _open_whatsapp_dialog(self) -> None:
        win = tk.Toplevel(self)
        win.title("WhatsApp Alerts")
        win.configure(bg=self._bg)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")

        ttk.Checkbutton(frm, text="Enable WhatsApp alerts", variable=self.whatsapp_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        ttk.Label(frm, text="To number (E.164)").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 6))
        ttk.Entry(frm, textvariable=self.whatsapp_to_var, width=26).grid(row=1, column=1, sticky="w", pady=(0, 6))

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="Send test", command=self._test_whatsapp).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="Close", command=win.destroy).grid(row=0, column=1)

    def _start(self) -> None:
        # Only allow start if connected
        if not self.client.initialize():
            self.connected_var.set("Disconnected")
            self._append_log("ERROR | Not connected to MT5. Click Connect first.")
            return
        
        # Create bot instance if not exists
        if self.bot is None:
            cfg = BotConfig(
                symbol=self.symbol_var.get().strip(),
                timeframe=self.timeframe_var.get(),
                test_mode=self.test_mode_var.get(),
                auto_calibrate=True,  # Always use continuous calibration
            )
            self.bot = TradingBot(cfg, self.event_q)
            
            # Initialize continuous calibrator
            self.continuous_calibrator = ContinuousCalibrator(self.client, cfg)
            self.bot._trade_monitor = self.trade_monitor
            self.bot._continuous_calibrator = self.continuous_calibrator
        
        # Start the bot
        self.bot.start()
        
        # Start continuous calibration
        if self.continuous_calibrator:
            self.continuous_calibrator.start_continuous_calibration()
            self._append_log("INFO | Continuous edge calibration started.")
        
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._append_log("INFO | Bot started.")
        
        try:
            acc = self.client.account_info()
            login_id = getattr(acc, "login", None)
            server_name = getattr(acc, "server", None)
            self.connected_var.set(f"Connected ({login_id}@{server_name})")
            self._append_log(f"INFO | Connected to account login={login_id} server={server_name}")
        except Exception as e:
            self.connected_var.set("Connected (no account)")
            self._append_log(f"ERROR | Connected to terminal but account_info failed: {e}")

    def _connect(self) -> None:
        """Connect to MT5 with current settings"""
        try:
            # Get connection settings
            terminal_path = self.mt5_path_var.get() or None
            login = self.mt5_login_var.get()
            password = self.mt5_pass_var.get()
            server = self.mt5_server_var.get()
            
            # Configure client
            self.client.configure(
                terminal_path=terminal_path,
                login=int(login) if login else None,
                password=password or None,
                server=server or None
            )
            
            # Attempt connection
            if self.client.initialize():
                self.is_logged_in = True
                self.connected_var.set("Connected")
                self._append_log("INFO | MT5 connection successful")
                
                # Load market watch symbols
                self._load_market_watch_symbols()
                
                # Get account info
                try:
                    acc = self.client.account_info()
                    login_id = getattr(acc, "login", None)
                    server_name = getattr(acc, "server", None)
                    self.connected_var.set(f"Connected ({login_id}@{server_name})")
                    self._append_log(f"INFO | Connected to account login={login_id} server={server_name}")
                except Exception as e:
                    self._append_log(f"WARN | Connected but account info failed: {e}")
                
                # Enable start button
                self.start_btn.configure(state="normal")
                
            else:
                self.connected_var.set("Disconnected")
                self._append_log("ERROR | MT5 connection failed")
                
        except Exception as e:
            self.connected_var.set("Disconnected")
            self._append_log(f"ERROR | Connection failed: {e}")

    def _stop(self) -> None:
        """Stop the trading bot"""
        if self.bot is None:
            return
        
        try:
            # Stop continuous calibration
            if self.continuous_calibrator:
                self.continuous_calibrator.stop_continuous_calibration()
                self._append_log("INFO | Continuous edge calibration stopped.")
            
            # Stop the bot
            self.bot.stop()
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self._append_log("INFO | Bot stopped.")
        except Exception as e:
            self._append_log(f"ERROR | Failed to stop bot: {e}")

    def _test_whatsapp(self) -> None:
        if not bool(self.whatsapp_enabled_var.get()):
            self._append_log("INFO | Enable WhatsApp alerts first.")
            return
        wa = WhatsAppNotifier(WhatsAppConfig(enabled=True, to_number=self.whatsapp_to_var.get().strip(), min_interval_sec=0.0))
        try:
            wa.send("MT5 Bot test alert: WhatsApp notifications are working.")
            self._append_log("INFO | WhatsApp test alert sent.")
        except Exception as e:
            self._append_log(f"ERROR | WhatsApp test failed: {type(e).__name__}: {e}")


def run_app() -> None:
    app = BotApp()
    app.mainloop()

