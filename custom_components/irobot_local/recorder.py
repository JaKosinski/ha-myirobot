"""Write every MQTT message from the robot to a JSON Lines file.

Used to find out what a robot reports locally (e.g. pose or map data during
a mission). One line per message: {"t": ISO time, "topic": ..., "payload": ...}.
The file contains network details (SSID, IP, MAC) - review it before sharing.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import threading
from typing import IO

_LOGGER = logging.getLogger(__name__)

MAX_BYTES = 50 * 1024 * 1024


class MessageRecorder:
    """Thread-safe JSONL writer, fed from the MQTT thread."""

    def __init__(self, max_bytes: int = MAX_BYTES) -> None:
        self.path: Path | None = None
        self.messages = 0
        self._max_bytes = max_bytes
        self._written = 0
        self._file: IO[str] | None = None
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        return self._file is not None

    def start(self, path: Path) -> None:
        """Open a new file. Blocking."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._close()
            self._file = path.open("a", encoding="utf-8")
            self.path = path
            self.messages = 0
            self._written = 0

    def stop(self) -> None:
        """Close the file. Blocking."""
        with self._lock:
            self._close()

    def handle(self, topic: str, payload: bytes) -> None:
        """Append one message; stops by itself past max_bytes."""
        with self._lock:
            if self._file is None:
                return
            try:
                decoded: object = json.loads(payload)
            except ValueError:
                decoded = payload.decode("utf-8", "backslashreplace")
            line = json.dumps(
                {
                    "t": datetime.now(UTC).isoformat(timespec="milliseconds"),
                    "topic": topic,
                    "payload": decoded,
                },
                ensure_ascii=False,
            )
            self._file.write(line + "\n")
            self._file.flush()
            self.messages += 1
            self._written += len(line) + 1
            if self._written >= self._max_bytes:
                _LOGGER.warning("Recording %s reached its size limit, stopping", self.path)
                self._close()

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
