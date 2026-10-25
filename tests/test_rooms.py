"""Tests for room parsing and the room clean command."""

from __future__ import annotations

import pytest

from custom_components.irobot_local.rooms import (
    RoomsParseError,
    clean_rooms_params,
    format_rooms,
    parse_rooms,
    segment_id,
    suggest_from_last_command,
)


def test_parse_and_format_roundtrip() -> None:
    text = "5 = Kuchnia\n\n# komentarz\nzid:3 = Pod stołem\n12=Salon"
    rooms = parse_rooms(text)
    assert rooms == [
        {"id": "5", "type": "rid", "name": "Kuchnia"},
        {"id": "3", "type": "zid", "name": "Pod stołem"},
        {"id": "12", "type": "rid", "name": "Salon"},
    ]
    assert [segment_id(room) for room in rooms] == ["5", "zid:3", "12"]
    assert parse_rooms(format_rooms(rooms)) == rooms


@pytest.mark.parametrize("text", ["5 Kuchnia", "= Kuchnia", "5 =", "zid: = Strefa", "5 = A\n5 = B"])
def test_parse_errors(text: str) -> None:
    with pytest.raises(RoomsParseError):
        parse_rooms(text)


def test_room_and_zone_with_same_id_allowed() -> None:
    assert len(parse_rooms("3 = Pokój\nzid:3 = Strefa")) == 2


def test_suggest_from_last_command() -> None:
    last_command = {
        "command": "start",
        "pmap_id": "abc",
        "user_pmapv_id": "190917T20125Z",
        "regions": [{"region_id": "5", "type": "rid"}, {"region_id": "3", "type": "zid"}],
    }
    assert suggest_from_last_command(last_command) == {
        "pmap_id": "abc",
        "user_pmapv_id": "190917T20125Z",
        "rooms": "5 = Room 5\nzid:3 = Room 3",
    }
    assert suggest_from_last_command(None) == {}
    assert suggest_from_last_command({"command": "dock"}) == {}


def test_clean_rooms_params_keeps_order() -> None:
    rooms = parse_rooms("12 = Salon\n5 = Kuchnia")
    assert clean_rooms_params("abc", "v1", rooms) == {
        "pmap_id": "abc",
        "user_pmapv_id": "v1",
        "ordered": 1,
        "regions": [
            {"region_id": "12", "type": "rid"},
            {"region_id": "5", "type": "rid"},
        ],
    }
    assert "user_pmapv_id" not in clean_rooms_params("abc", "", rooms)
