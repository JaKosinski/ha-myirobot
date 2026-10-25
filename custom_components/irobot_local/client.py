"""Local MQTT/TLS client for Wi-Fi iRobot Roomba robots (i-series, 9xx, e5...).

Independent from Home Assistant so it can be tested and used standalone.
The robot accepts only one local MQTT connection at a time, so close the
iRobot app and any other integration talking to the same robot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import json
import logging
import ssl
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8883
CONNECT_TIMEOUT = 10

# Tried in order until the TLS handshake and CONNACK succeed. Roomba firmware
# uses a self-signed, weak certificate, hence SECLEVEL=0. TLS 1.3 suites
# (e.g. TLS_AES_256_GCM_SHA384) are always offered by OpenSSL in addition.
CIPHER_PROFILES = ("AES128-SHA256:@SECLEVEL=0", "DEFAULT:@SECLEVEL=0")

# MQTT 3.1.1 CONNACK codes as mapped by paho 2.x ReasonCode values.
_AUTH_FAILURE_CODES = (134, 135)

ACTIVITY_CLEANING = "cleaning"
ACTIVITY_DOCKED = "docked"
ACTIVITY_IDLE = "idle"
ACTIVITY_PAUSED = "paused"
ACTIVITY_RETURNING = "returning"
ACTIVITY_ERROR = "error"


class RoombaError(Exception):
    """Base error."""


class RoombaConnectionError(RoombaError):
    """Robot unreachable, TLS handshake failed or connection refused."""


class RoombaAuthError(RoombaError):
    """BLID or password rejected by the robot."""


def mission_activity(state: dict[str, Any]) -> str | None:
    """Map cleanMissionStatus to a vacuum activity name."""
    status = state.get("cleanMissionStatus") or {}
    phase = status.get("phase")
    if phase is None:
        return None
    cycle = status.get("cycle")
    if phase in ("stuck", "chargingerror"):
        return ACTIVITY_ERROR
    if phase == "run":
        return ACTIVITY_CLEANING
    if phase.startswith("hm"):  # hmUsrDock, hmMidMsn, hmPostMsn
        return ACTIVITY_RETURNING
    if phase in ("charge", "evac", "recharge"):
        return ACTIVITY_DOCKED
    if phase in ("stop", "pause") and cycle not in (None, "none"):
        return ACTIVITY_PAUSED
    return ACTIVITY_IDLE


class RoombaClient:
    """Keeps one MQTT connection to the robot and mirrors its reported state."""

    def __init__(
        self, host: str, blid: str, password: str, port: int = DEFAULT_PORT
    ) -> None:
        self.host = host
        self.blid = blid
        self._password = password
        self.port = port
        self.state: dict[str, Any] = {}
        self.connected = False
        self._client: mqtt.Client | None = None
        self._ciphers: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._raw_listeners: list[Callable[[str, bytes], None]] = []
        self._cond = threading.Condition()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired (from the MQTT thread) on any change."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def add_raw_listener(
        self, listener: Callable[[str, bytes], None]
    ) -> Callable[[], None]:
        """Register a callback fired (from the MQTT thread) with every message."""
        self._raw_listeners.append(listener)
        return lambda: self._raw_listeners.remove(listener)

    def connect(self, timeout: float = CONNECT_TIMEOUT) -> None:
        """Connect, trying each cipher profile. Blocking."""
        profiles = list(CIPHER_PROFILES)
        if self._ciphers in profiles:
            profiles.remove(self._ciphers)
            profiles.insert(0, self._ciphers)

        last_error: Exception | None = None
        for ciphers in profiles:
            try:
                self._connect_once(ciphers, timeout)
            except RoombaAuthError:
                raise
            except RoombaConnectionError as err:
                _LOGGER.debug("Connect with ciphers %s failed: %s", ciphers, err)
                last_error = err
            else:
                self._ciphers = ciphers
                return
        raise RoombaConnectionError(str(last_error))

    def disconnect(self) -> None:
        """Close the connection. Blocking."""
        client, self._client = self._client, None
        if client is not None:
            client.disconnect()
            client.loop_stop()
        self._set_connected(False)

    def wait_for(self, keys: Iterable[str], timeout: float) -> bool:
        """Block until all keys are present in the reported state."""
        keys = tuple(keys)
        with self._cond:
            return self._cond.wait_for(
                lambda: all(key in self.state for key in keys), timeout
            )

    def send_command(self, command: str, **params: Any) -> None:
        """Send a mission command (start, pause, resume, stop, dock, find, evac)."""
        payload = {
            "command": command,
            "time": int(time.time()),
            "initiator": "localApp",
            **params,
        }
        self._publish("cmd", payload)

    def set_preferences(self, **values: Any) -> None:
        """Change robot settings (e.g. carpetBoost, openOnly, binPause)."""
        self._publish("delta", {"state": values})

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        if self._client is None or not self.connected:
            raise RoombaConnectionError("Not connected to the robot")
        info = self._client.publish(topic, json.dumps(payload))
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RoombaConnectionError(f"Publish failed: {mqtt.error_string(info.rc)}")

    def _connect_once(self, ciphers: str, timeout: float) -> None:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.blid,
            clean_session=False,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self.blid, self._password)
        client.tls_set_context(ssl_context(ciphers))
        client.reconnect_delay_set(min_delay=1, max_delay=60)

        connack = threading.Event()
        result: dict[str, Any] = {}

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            result["rc"] = reason_code
            connack.set()
            if not reason_code.is_failure:
                _LOGGER.debug("Connected to %s", self.host)
                self._set_connected(True)

        def on_disconnect(_client, _userdata, _flags, reason_code, _properties):
            _LOGGER.debug("Disconnected from %s: %s", self.host, reason_code)
            self._set_connected(False)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = self._on_message

        try:
            client.connect(self.host, self.port, keepalive=60)
        except (OSError, ssl.SSLError) as err:
            raise RoombaConnectionError(f"{type(err).__name__}: {err}") from err

        client.loop_start()
        if not connack.wait(timeout):
            _stop(client)
            raise RoombaConnectionError("Timed out waiting for CONNACK")

        rc = result["rc"]
        if rc.is_failure:
            _stop(client)
            if rc.value in _AUTH_FAILURE_CODES:
                raise RoombaAuthError(str(rc))
            raise RoombaConnectionError(f"Connection refused: {rc}")

        self._client = client

    def _on_message(self, _client, _userdata, message: mqtt.MQTTMessage) -> None:
        for raw_listener in list(self._raw_listeners):
            try:
                raw_listener(message.topic, message.payload)
            except Exception:
                _LOGGER.exception("Error in Roomba raw message listener")
        try:
            reported = json.loads(message.payload)["state"]["reported"]
        except (ValueError, KeyError, TypeError):
            return
        if not isinstance(reported, dict):
            return
        with self._cond:
            self.state.update(reported)
            self._cond.notify_all()
        self._notify()

    def _set_connected(self, connected: bool) -> None:
        if self.connected != connected:
            self.connected = connected
            self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("Error in Roomba state listener")


def ssl_context(ciphers: str) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_ciphers(ciphers)
    context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
    return context


def _stop(client: mqtt.Client) -> None:
    client.disconnect()
    client.loop_stop()
