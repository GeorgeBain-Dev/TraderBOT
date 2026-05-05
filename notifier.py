from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

try:
    from .utils import setup_logging, utc_now
except ImportError:
    from utils import setup_logging, utc_now

logger = setup_logging()


class NotificationError(RuntimeError):
    pass


@dataclass
class WhatsAppConfig:
    enabled: bool = False
    to_number: str = ""  # e.g. +49123456789
    min_interval_sec: float = 15.0


class WhatsAppNotifier:
    """Twilio WhatsApp notifier (optional).

    Requires environment variables:
    - TWILIO_ACCOUNT_SID
    - TWILIO_AUTH_TOKEN
    - TWILIO_WHATSAPP_FROM   (e.g. "whatsapp:+14155238886")
    """

    def __init__(self, cfg: WhatsAppConfig):
        self.cfg = cfg
        self._last_sent_ts = 0.0

    def _ready(self) -> bool:
        if not self.cfg.enabled:
            return False
        if not self.cfg.to_number.strip():
            return False
        return True

    def send(self, message: str) -> bool:
        if not self._ready():
            return False

        now = time.time()
        if (now - self._last_sent_ts) < self.cfg.min_interval_sec:
            return False

        sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        from_wa = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
        if not (sid and token and from_wa):
            raise NotificationError(
                "Missing Twilio env vars: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_WHATSAPP_FROM"
            )

        try:
            from twilio.rest import Client  # type: ignore
        except Exception as e:  # pragma: no cover
            raise NotificationError(f"Twilio package not available: {e}") from e

        to_wa = self.cfg.to_number.strip()
        if not to_wa.lower().startswith("whatsapp:"):
            to_wa = "whatsapp:" + to_wa

        client = Client(sid, token)
        client.messages.create(from_=from_wa, to=to_wa, body=message)
        self._last_sent_ts = now
        logger.info("WhatsApp alert sent.")
        return True


@dataclass
class ChangeDetector:
    last_mode: str = ""
    last_signal: str = ""
    last_positions_count: int = -1

    def format_change_message(
        self,
        *,
        symbol: str,
        mode: str,
        signal: str,
        price: float,
        positions_count: int,
    ) -> Optional[str]:
        changed = False
        lines = []
        if mode and mode != self.last_mode:
            changed = True
            lines.append(f"Mode: {self.last_mode or '—'} -> {mode}")
            self.last_mode = mode
        if signal and signal != self.last_signal:
            changed = True
            lines.append(f"Signal: {self.last_signal or '—'} -> {signal}")
            self.last_signal = signal
        if positions_count != self.last_positions_count:
            changed = True
            lines.append(f"Positions: {self.last_positions_count if self.last_positions_count >= 0 else '—'} -> {positions_count}")
            self.last_positions_count = positions_count

        if not changed:
            return None

        header = f"{symbol} @ {price:.5f}" if price else symbol
        return header + "\n" + "\n".join(lines)

