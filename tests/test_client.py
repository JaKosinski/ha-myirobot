"""Tests for the standalone Roomba client against a fake TLS MQTT robot."""

from __future__ import annotations

import json
import socket
import ssl
import threading

import pytest

from custom_components.irobot_local.client import (
    RoombaAuthError,
    RoombaClient,
    RoombaConnectionError,
    mission_activity,
)

# Real sockets: a fake robot runs on localhost.
pytestmark = pytest.mark.usefixtures("socket_enabled")

BLID = "0123456789ABCDEF0123456789ABCDEF"
PASSWORD = ":1:1600000000:testpassword"


def _read_varint(conn) -> int:
    value, shift = 0, 0
    while True:
        byte = conn.recv(1)[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7


def _read_packet(conn) -> tuple[int, bytes]:
    header = conn.recv(1)
    if not header:
        raise ConnectionError
    length = _read_varint(conn)
    body = b""
    while len(body) < length:
        body += conn.recv(length - len(body))
    return header[0], body


def _string(data: bytes, offset: int) -> tuple[str, int]:
    size = int.from_bytes(data[offset : offset + 2], "big")
    return data[offset + 2 : offset + 2 + size].decode(), offset + 2 + size


def _publish(topic: str, payload: dict) -> bytes:
    body = len(topic).to_bytes(2, "big") + topic.encode() + json.dumps(payload).encode()
    length, size = bytearray(), len(body)
    while True:
        byte, size = size % 128, size // 128
        length.append(byte | (0x80 if size else 0))
        if not size:
            break
    return bytes([0x30]) + bytes(length) + body


class FakeRobot:
    """Speaks just enough MQTT 3.1.1 over TLS 1.2 / AES128-SHA256 like a Roomba."""

    def __init__(self, cert_files) -> None:
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(*cert_files)
        self.context.maximum_version = ssl.TLSVersion.TLSv1_2
        self.context.set_ciphers("AES128-SHA256:@SECLEVEL=0")
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.received: list[tuple[str, dict]] = []
        self.got_publish = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(raw,), daemon=True).start()

    def _handle(self, raw) -> None:
        try:
            conn = self.context.wrap_socket(raw, server_side=True)
            _, body = _read_packet(conn)
            offset = 10  # protocol name, level, flags, keepalive
            client_id, offset = _string(body, offset)
            username, offset = _string(body, offset)
            password, offset = _string(body, offset)
            if (client_id, username, password) != (BLID, BLID, PASSWORD):
                conn.sendall(b"\x20\x02\x00\x05")  # not authorized
                return
            conn.sendall(b"\x20\x02\x00\x00")
            topic = f"$aws/things/{BLID}/shadow/update"
            conn.sendall(_publish(topic, {"state": {"reported": {"name": "Roomba", "batPct": 87}}}))
            conn.sendall(
                _publish(
                    topic,
                    {"state": {"reported": {"cleanMissionStatus": {"cycle": "none", "phase": "charge"}}}},
                )
            )
            while True:
                kind, body = _read_packet(conn)
                if kind >> 4 == 3:  # PUBLISH, QoS 0
                    topic, offset = _string(body, 0)
                    self.received.append((topic, json.loads(body[offset:])))
                    self.got_publish.set()
                elif kind >> 4 == 14:  # DISCONNECT
                    return
        except (ConnectionError, OSError, IndexError):
            return

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def robot(cert_files):
    robot = FakeRobot(cert_files)
    yield robot
    robot.close()


def test_connect_state_and_command(robot) -> None:
    client = RoombaClient("127.0.0.1", BLID, PASSWORD, port=robot.port)
    client.connect(timeout=5)
    try:
        assert client.connected
        assert client.wait_for(("name", "batPct", "cleanMissionStatus"), 5)
        assert client.state["batPct"] == 87
        assert mission_activity(client.state) == "docked"

        client.send_command("start")
        assert robot.got_publish.wait(5)
        topic, payload = robot.received[0]
        assert topic == "cmd"
        assert payload["command"] == "start"
        assert payload["initiator"] == "localApp"
    finally:
        client.disconnect()
    assert not client.connected


def test_wrong_password(robot) -> None:
    client = RoombaClient("127.0.0.1", BLID, "wrong", port=robot.port)
    with pytest.raises(RoombaAuthError):
        client.connect(timeout=5)


def test_password_without_leading_colon_rejected(robot) -> None:
    client = RoombaClient("127.0.0.1", BLID, PASSWORD.lstrip(":"), port=robot.port)
    with pytest.raises(RoombaAuthError):
        client.connect(timeout=5)


def test_unreachable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as sock:
        port = sock.getsockname()[1]
    client = RoombaClient("127.0.0.1", BLID, PASSWORD, port=port)
    with pytest.raises(RoombaConnectionError):
        client.connect(timeout=2)


def test_send_without_connection() -> None:
    client = RoombaClient("127.0.0.1", BLID, PASSWORD)
    with pytest.raises(RoombaConnectionError):
        client.send_command("start")


@pytest.mark.parametrize(
    ("cycle", "phase", "expected"),
    [
        ("clean", "run", "cleaning"),
        ("quick", "run", "cleaning"),
        ("clean", "hmUsrDock", "returning"),
        ("clean", "hmPostMsn", "returning"),
        ("none", "charge", "docked"),
        ("clean", "charge", "docked"),
        ("none", "evac", "docked"),
        ("clean", "stop", "paused"),
        ("none", "stop", "idle"),
        ("clean", "stuck", "error"),
        ("none", "chargingerror", "error"),
    ],
)
def test_mission_activity(cycle, phase, expected) -> None:
    state = {"cleanMissionStatus": {"cycle": cycle, "phase": phase}}
    assert mission_activity(state) == expected


def test_mission_activity_unknown() -> None:
    assert mission_activity({}) is None
