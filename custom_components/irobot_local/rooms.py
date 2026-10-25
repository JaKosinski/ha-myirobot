"""Room (region) configuration for selective cleaning.

The robot only knows numeric region ids of its Smart Map; names live in the
iRobot cloud. Users type them in the options flow, one per line:

    5 = Kitchen
    zid:3 = Under the table

A bare id is a room (type "rid"), "zid:<id>" is a custom zone.
"""

from __future__ import annotations

from typing import Any

TYPE_ROOM = "rid"
TYPE_ZONE = "zid"


class RoomsParseError(ValueError):
    """Invalid line in the rooms text."""


def segment_id(room: dict[str, str]) -> str:
    """Stable id used by Home Assistant to map a room to an area."""
    if room["type"] == TYPE_ZONE:
        return f"{TYPE_ZONE}:{room['id']}"
    return room["id"]


def parse_rooms(text: str) -> list[dict[str, str]]:
    """Parse "<id> = <name>" lines."""
    rooms: list[dict[str, str]] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        region, sep, name = (part.strip() for part in line.partition("="))
        if not sep or not region or not name:
            raise RoomsParseError(f"Line {number}: expected '<id> = <name>'")
        kind = TYPE_ROOM
        if region.lower().startswith(f"{TYPE_ZONE}:"):
            kind, region = TYPE_ZONE, region[len(TYPE_ZONE) + 1 :].strip()
        if not region:
            raise RoomsParseError(f"Line {number}: missing region id")
        room = {"id": region, "type": kind, "name": name}
        if segment_id(room) in seen:
            raise RoomsParseError(f"Line {number}: duplicate region {region}")
        seen.add(segment_id(room))
        rooms.append(room)
    return rooms


def format_rooms(rooms: list[dict[str, str]]) -> str:
    """Inverse of parse_rooms."""
    return "\n".join(f"{segment_id(room)} = {room['name']}" for room in rooms)


def suggest_from_last_command(last_command: dict[str, Any] | None) -> dict[str, str]:
    """Prefill options from a room clean started in the iRobot app."""
    if not isinstance(last_command, dict):
        return {}
    suggestion: dict[str, str] = {}
    for key in ("pmap_id", "user_pmapv_id"):
        if last_command.get(key):
            suggestion[key] = str(last_command[key])
    rooms = [
        {
            "id": str(region["region_id"]),
            "type": region.get("type") or TYPE_ROOM,
            "name": f"Room {region['region_id']}",
        }
        for region in last_command.get("regions") or []
        if isinstance(region, dict) and "region_id" in region
    ]
    if rooms:
        suggestion["rooms"] = format_rooms(rooms)
    return suggestion


def clean_rooms_params(
    pmap_id: str, user_pmapv_id: str | None, rooms: list[dict[str, str]]
) -> dict[str, Any]:
    """Parameters of the "start" command cleaning the given rooms in order."""
    params: dict[str, Any] = {
        "pmap_id": pmap_id,
        "ordered": 1,
        "regions": [{"region_id": room["id"], "type": room["type"]} for room in rooms],
    }
    if user_pmapv_id:
        params["user_pmapv_id"] = user_pmapv_id
    return params
