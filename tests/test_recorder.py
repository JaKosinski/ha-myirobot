"""Tests for the raw MQTT message recorder."""

from __future__ import annotations

import json

from custom_components.irobot_local.client import RoombaClient
from custom_components.irobot_local.recorder import MessageRecorder


class _Message:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


def test_records_json_and_raw(tmp_path) -> None:
    recorder = MessageRecorder()
    path = tmp_path / "sub" / "rec.jsonl"
    recorder.handle("ignored", b"{}")  # not recording yet
    recorder.start(path)
    recorder.handle("$aws/things/X/shadow/update", b'{"state": {"reported": {"pose": {"point": {"x": 1}}}}}')
    recorder.handle("wifistat", b"\xffnot json")
    recorder.stop()
    recorder.handle("after", b"{}")

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert recorder.messages == 2
    assert [line["topic"] for line in lines] == ["$aws/things/X/shadow/update", "wifistat"]
    assert lines[0]["payload"]["state"]["reported"]["pose"]["point"]["x"] == 1
    assert lines[1]["payload"] == "\\xffnot json"
    assert "t" in lines[0]


def test_stops_at_size_limit(tmp_path) -> None:
    recorder = MessageRecorder(max_bytes=200)
    recorder.start(tmp_path / "rec.jsonl")
    for _ in range(10):
        recorder.handle("t", b'{"a": "' + b"x" * 50 + b'"}')
    assert not recorder.recording
    assert recorder.messages < 10


def test_client_feeds_raw_listener_every_message() -> None:
    client = RoombaClient("h", "B", "p")
    seen: list[tuple[str, bytes]] = []
    remove = client.add_raw_listener(lambda topic, payload: seen.append((topic, payload)))
    client._on_message(None, None, _Message("wifistat", b'{"x": 1}'))
    client._on_message(None, None, _Message("shadow", b'{"state": {"reported": {"batPct": 5}}}'))
    remove()
    client._on_message(None, None, _Message("late", b"{}"))
    assert [topic for topic, _ in seen] == ["wifistat", "shadow"]
    assert client.state == {"batPct": 5}
