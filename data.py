from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pandas as pd

try:
    from .utils import setup_logging
except ImportError:
    from utils import setup_logging

logger = setup_logging()


TIMEFRAME_MAP: Dict[str, int] = {}
try:
    import MetaTrader5 as mt5  # type: ignore

    TIMEFRAME_MAP = {
        "M1": mt5.TIMEFRAME_M1,
        "M2": mt5.TIMEFRAME_M2,
        "M3": mt5.TIMEFRAME_M3,
        "M4": mt5.TIMEFRAME_M4,
        "M5": mt5.TIMEFRAME_M5,
        "M6": mt5.TIMEFRAME_M6,
        "M10": mt5.TIMEFRAME_M10,
        "M12": mt5.TIMEFRAME_M12,
        "M15": mt5.TIMEFRAME_M15,
        "M20": mt5.TIMEFRAME_M20,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H2": mt5.TIMEFRAME_H2,
        "H3": mt5.TIMEFRAME_H3,
        "H4": mt5.TIMEFRAME_H4,
        "H6": mt5.TIMEFRAME_H6,
        "H8": mt5.TIMEFRAME_H8,
        "H12": mt5.TIMEFRAME_H12,
        "D1": mt5.TIMEFRAME_D1,
        "W1": mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1,
    }
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore


class MT5Error(RuntimeError):
    pass


def _require_mt5() -> Any:
    if mt5 is None:
        raise MT5Error(
            "MetaTrader5 package is not available. Install it with `pip install MetaTrader5`."
        )
    return mt5


def _to_utc_datetime(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, unit="s", utc=True)
    return dt


def rates_to_df(rates: Any) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    if df.empty:
        return df
    if "time" in df.columns:
        df["time"] = _to_utc_datetime(df["time"])
        df = df.set_index("time")
    return df


@dataclass
class MT5Client:
    initialized: bool = False
    terminal_path: Optional[str] = None
    login: Optional[int] = None
    password: Optional[str] = None
    server: Optional[str] = None

    def configure(
        self,
        *,
        terminal_path: Optional[str] = None,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
    ) -> None:
        # Credentials are held in memory only; never persisted.
        self.terminal_path = terminal_path or None
        self.login = login
        self.password = password or None
        self.server = server or None

    def initialize(self) -> bool:
        mt5_mod = _require_mt5()
        if self.initialized:
            return True
        kwargs: Dict[str, Any] = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        if self.login is not None:
            kwargs["login"] = int(self.login)
        if self.password:
            kwargs["password"] = self.password
        if self.server:
            kwargs["server"] = self.server

        ok = mt5_mod.initialize(**kwargs) if kwargs else mt5_mod.initialize()
        if not ok:
            err = mt5_mod.last_error()
            logger.error("MT5 initialize failed: %s", err)
            self.initialized = False
            return False
        self.initialized = True
        return True

    def connect(
        self,
        *,
        terminal_path: Optional[str] = None,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
    ) -> bool:
        # Fresh init attempt
        self.shutdown()
        self.configure(
            terminal_path=terminal_path,
            login=login,
            password=password,
            server=server,
        )
        return self.initialize()

    def shutdown(self) -> None:
        if mt5 is None:
            return
        try:
            mt5.shutdown()
        except Exception:
            pass
        self.initialized = False

    def ensure_symbol(self, symbol: str) -> None:
        mt5_mod = _require_mt5()
        info = mt5_mod.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"Symbol not found in MT5: {symbol}")
        if not info.visible:
            if not mt5_mod.symbol_select(symbol, True):
                raise MT5Error(f"Failed to select symbol: {symbol}")

    def account_info(self) -> Any:
        mt5_mod = _require_mt5()
        info = mt5_mod.account_info()
        if info is None:
            raise MT5Error(f"account_info unavailable: {mt5_mod.last_error()}")
        return info

    def symbol_info(self, symbol: str) -> Any:
        mt5_mod = _require_mt5()
        info = mt5_mod.symbol_info(symbol)
        if info is None:
            raise MT5Error(f"symbol_info unavailable for {symbol}: {mt5_mod.last_error()}")
        return info

    def tick(self, symbol: str) -> Any:
        mt5_mod = _require_mt5()
        tick = mt5_mod.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"tick unavailable for {symbol}: {mt5_mod.last_error()}")
        return tick

    def symbols_get(self) -> Any:
        """Get all available symbols from MT5"""
        mt5_mod = _require_mt5()
        symbols = mt5_mod.symbols_get()
        if symbols is None:
            raise MT5Error(f"symbols_get unavailable: {mt5_mod.last_error()}")
        return symbols

    def login_to_account(self, login: int, password: str, server: str) -> bool:
        """Login to MT5 account"""
        mt5_mod = _require_mt5()
        if not self.initialize():
            return False
        result = mt5_mod.login(login, password, server)
        if not result:
            logger.error(f"MT5 login failed: {mt5_mod.last_error()}")
        return result


def get_latest_candles(
    client: MT5Client,
    symbol: str,
    timeframe: str,
    n: int = 300,
) -> pd.DataFrame:
    mt5_mod = _require_mt5()
    if not client.initialize():
        raise MT5Error("MT5 is not initialized. Ensure MT5 terminal is running.")
    client.ensure_symbol(symbol)
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    rates = mt5_mod.copy_rates_from_pos(symbol, tf, 0, int(n))
    df = rates_to_df(rates)
    return df


def get_historical_data(
    client: MT5Client,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    mt5_mod = _require_mt5()
    if not client.initialize():
        raise MT5Error("MT5 is not initialized. Ensure MT5 terminal is running.")
    client.ensure_symbol(symbol)
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    # MT5 expects naive datetimes in local tz OR aware; safest: pass UTC naive
    s = start.astimezone(timezone.utc).replace(tzinfo=None)
    e = end.astimezone(timezone.utc).replace(tzinfo=None)
    rates = mt5_mod.copy_rates_range(symbol, tf, s, e)
    df = rates_to_df(rates)
    return df


def get_live_price(client: MT5Client, symbol: str) -> Tuple[float, float, datetime]:
    if not client.initialize():
        raise MT5Error("MT5 is not initialized. Ensure MT5 terminal is running.")
    client.ensure_symbol(symbol)
    t = client.tick(symbol)
    bid = float(getattr(t, "bid", 0.0))
    ask = float(getattr(t, "ask", 0.0))
    ts = datetime.fromtimestamp(int(getattr(t, "time", 0)), tz=timezone.utc)
    return bid, ask, ts

