from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable

from .data import MT5Client, MT5Error
from .utils import setup_logging

logger = setup_logging()


class LoginScreen(tk.Toplevel):
    def __init__(self, parent: tk.Tk, on_login_success: Callable[[MT5Client], None]) -> None:
        super().__init__(parent)
        self.parent = parent
        self.on_login_success = on_login_success
        self.client = MT5Client()
        
        self.title("MT5 Login - Trading Bot")
        self.geometry("450x350")
        self.resizable(False, False)
        self._init_style()
        self._build_ui()
        
        # Center the window
        self.transient(parent)
        self.grab_set()
        
        # Focus on login field
        self.login_entry.focus()
        
        # Bind Enter key
        self.bind('<Return>', lambda e: self._login())
        
    def _init_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Modern dark theme
        self._bg = "#0a0a0f"
        self._panel = "#1a1a2e"
        self._card_bg = "#16213e"
        self._accent = "#e94560"
        self._success = "#00d4aa"
        self._fg = "#f5f5f5"
        self._muted = "#8892b0"
        self._border = "#233554"

        self.configure(bg=self._bg)
        style.configure(".", background=self._bg, foreground=self._fg, fieldbackground=self._panel)
        style.configure("TFrame", background=self._bg)
        style.configure("Card.TFrame", background=self._card_bg)
        
        # Modern fonts
        style.configure("TLabel", background=self._bg, foreground=self._fg, font=("Inter", 10))
        style.configure("Title.TLabel", background=self._card_bg, foreground=self._accent, font=("Inter", 16, "bold"))
        style.configure("SubTitle.TLabel", background=self._card_bg, foreground=self._muted, font=("Inter", 9))
        
        # Modern buttons
        style.configure("TButton", padding=(12, 8), background=self._card_bg, foreground=self._fg, 
                       borderwidth=1, relief="flat", font=("Inter", 10, "bold"))
        style.map("TButton", background=[("active", self._accent)], foreground=[("active", "#ffffff")])
        
        style.configure("Login.TButton", background=self._success, foreground="#000000", 
                       font=("Inter", 12, "bold"))
        style.map("Login.TButton", background=[("active", "#00b894")])
        
        # Modern inputs
        style.configure("TEntry", fieldbackground=self._panel, background=self._panel, 
                       foreground=self._fg, bordercolor=self._border, relief="flat")

    def _build_ui(self) -> None:
        # Main container
        main_frame = ttk.Frame(self, padding=20, style="Card.TFrame")
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Title
        title_frame = ttk.Frame(main_frame, style="Card.TFrame")
        title_frame.pack(fill="x", pady=(0, 20))
        
        ttk.Label(title_frame, text="🔐 MT5 Login", style="Title.TLabel").pack()
        ttk.Label(title_frame, text="Connect to your MetaTrader 5 account", style="SubTitle.TLabel").pack(pady=(5, 0))
        
        # Login form
        form_frame = ttk.Frame(main_frame, style="Card.TFrame")
        form_frame.pack(fill="x", pady=10)
        
        # Login field
        ttk.Label(form_frame, text="Login:", style="TLabel").grid(row=0, column=0, sticky="w", pady=5)
        self.login_entry = ttk.Entry(form_frame, width=25, font=("Inter", 10))
        self.login_entry.grid(row=0, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Password field
        ttk.Label(form_frame, text="Password:", style="TLabel").grid(row=1, column=0, sticky="w", pady=5)
        self.password_entry = ttk.Entry(form_frame, width=25, show="*", font=("Inter", 10))
        self.password_entry.grid(row=1, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Server field
        ttk.Label(form_frame, text="Server:", style="TLabel").grid(row=2, column=0, sticky="w", pady=5)
        self.server_entry = ttk.Entry(form_frame, width=25, font=("Inter", 10))
        self.server_entry.grid(row=2, column=1, sticky="ew", pady=5, padx=(10, 0))
        
        # Path field (optional)
        ttk.Label(form_frame, text="MT5 Path:", style="TLabel").grid(row=3, column=0, sticky="w", pady=5)
        self.path_entry = ttk.Entry(form_frame, width=25, font=("Inter", 10))
        self.path_entry.grid(row=3, column=1, sticky="ew", pady=5, padx=(10, 0))
        ttk.Label(form_frame, text="(Optional)", style="SubTitle.TLabel").grid(row=4, column=1, sticky="w", pady=(0, 10))
        
        form_frame.columnconfigure(1, weight=1)
        
        # Status label
        self.status_label = ttk.Label(main_frame, text="Enter your MT5 credentials to continue", 
                                    style="SubTitle.TLabel")
        self.status_label.pack(pady=10)
        
        # Buttons
        button_frame = ttk.Frame(main_frame, style="Card.TFrame")
        button_frame.pack(fill="x", pady=(10, 0))
        
        self.login_button = ttk.Button(button_frame, text="🚀 Login", command=self._login, 
                                      style="Login.TButton")
        self.login_button.pack(side="left", padx=(0, 10))
        
        self.cancel_button = ttk.Button(button_frame, text="Cancel", command=self._cancel)
        self.cancel_button.pack(side="left")
        
        # Quick connect button (for demo/testing)
        demo_frame = ttk.Frame(main_frame, style="Card.TFrame")
        demo_frame.pack(fill="x", pady=(20, 0))
        
        ttk.Separator(demo_frame, orient="horizontal").pack(fill="x", pady=(0, 10))
        ttk.Button(demo_frame, text="🧪 Demo Mode (No Login)", command=self._demo_mode).pack()

    def _login(self) -> None:
        """Attempt to login to MT5"""
        login = self.login_entry.get().strip()
        password = self.password_entry.get().strip()
        server = self.server_entry.get().strip()
        path = self.path_entry.get().strip() or None
        
        if not login or not password or not server:
            self.status_label.configure(text="❌ Please fill in all required fields")
            return
        
        try:
            self.status_label.configure(text="🔄 Connecting to MT5...")
            self.login_button.configure(state="disabled")
            self.update()
            
            # Attempt to initialize and login
            if path:
                self.client.set_terminal_path(path)
            
            success = self.client.initialize()
            if not success:
                raise MT5Error("Failed to initialize MT5 terminal")
            
            # Try to login
            login_success = self.client.login(login=int(login), password=password, server=server)
            if not login_success:
                raise MT5Error("Login failed - check credentials")
            
            # Get account info to verify connection
            account_info = self.client.account_info()
            if not account_info:
                raise MT5Error("Failed to get account info")
            
            self.status_label.configure(text="✅ Login successful!")
            self.update()
            
            # Short delay before proceeding
            self.after(1000, lambda: self._on_login_success())
            
        except Exception as e:
            error_msg = f"❌ Login failed: {str(e)}"
            self.status_label.configure(text=error_msg)
            self.login_button.configure(state="normal")
            logger.error(f"Login error: {e}")

    def _demo_mode(self) -> None:
        """Start in demo mode without MT5 connection"""
        self.status_label.configure(text="🧪 Starting in demo mode...")
        self.update()
        
        # Create a demo client (won't connect to real MT5)
        demo_client = MT5Client()
        
        self.after(500, lambda: self.on_login_success(demo_client))

    def _on_login_success(self) -> None:
        """Handle successful login"""
        try:
            self.on_login_success(self.client)
            self.destroy()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to proceed: {e}")
            self.login_button.configure(state="normal")

    def _cancel(self) -> None:
        """Cancel login and close application"""
        if messagebox.askyesno("Cancel", "Are you sure you want to exit?"):
            self.parent.quit()
            self.destroy()


def show_login_screen(parent: tk.Tk, on_login_success: Callable[[MT5Client], None]) -> LoginScreen:
    """Show the login screen"""
    login_screen = LoginScreen(parent, on_login_success)
    parent.withdraw()  # Hide main window while login is open
    return login_screen
