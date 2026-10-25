"""Vacuum entity for iRobot Local."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IRobotConfigEntry
from .client import ACTIVITY_CLEANING, ACTIVITY_PAUSED, RoombaClient, RoombaError, mission_activity
from .const import CONF_PMAP_ID, CONF_ROOMS, CONF_USER_PMAPV_ID
from .entity import RoombaEntity
from .rooms import clean_rooms_params, segment_id

try:
    from homeassistant.components.vacuum import Segment
except ImportError:  # Home Assistant without vacuum area cleaning
    Segment = None

CLEAN_AREA = getattr(VacuumEntityFeature, "CLEAN_AREA", None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRobotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([RoombaVacuum(entry.runtime_data, dict(entry.options))])


class RoombaVacuum(RoombaEntity, StateVacuumEntity):
    """The robot itself."""

    _attr_name = None

    def __init__(self, client: RoombaClient, options: dict[str, Any]) -> None:
        super().__init__(client)
        self._pmap_id: str = options.get(CONF_PMAP_ID) or ""
        self._user_pmapv_id: str = options.get(CONF_USER_PMAPV_ID) or ""
        self._rooms: dict[str, dict[str, str]] = {
            segment_id(room): room for room in options.get(CONF_ROOMS) or []
        }
        features = (
            VacuumEntityFeature.START
            | VacuumEntityFeature.PAUSE
            | VacuumEntityFeature.STOP
            | VacuumEntityFeature.RETURN_HOME
            | VacuumEntityFeature.LOCATE
            | VacuumEntityFeature.STATE
            | VacuumEntityFeature.SEND_COMMAND
        )
        if self._rooms and self._pmap_id and CLEAN_AREA and Segment:
            features |= CLEAN_AREA
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if Segment is None:
            return
        # Rooms edited in the options: ask the user to remap them to areas.
        last_seen = self.last_seen_segments
        if last_seen is not None and self._rooms:
            current = [asdict(segment) for segment in await self.async_get_segments()]
            if [asdict(segment) for segment in last_seen] != current:
                self.async_create_segments_issue()

    @property
    def activity(self) -> VacuumActivity | None:
        activity = mission_activity(self.client.state)
        return VacuumActivity(activity) if activity else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.client.state
        status = state.get("cleanMissionStatus") or {}
        return {
            "cycle": status.get("cycle"),
            "phase": status.get("phase"),
            # Start a room clean from the iRobot app once, then the options
            # flow prefills the map and region ids from this.
            "last_command": state.get("lastCommand"),
        }

    async def async_get_segments(self) -> list[Any]:
        return [
            Segment(id=sid, name=room["name"]) for sid, room in self._rooms.items()
        ]

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        unknown = [sid for sid in segment_ids if sid not in self._rooms]
        if unknown:
            raise HomeAssistantError(f"Unknown Roomba regions: {', '.join(unknown)}")
        params = clean_rooms_params(
            self._pmap_id,
            self._user_pmapv_id,
            [self._rooms[sid] for sid in segment_ids],
        )
        await self._send("start", **params)

    async def async_start(self) -> None:
        command = "resume" if self.activity == ACTIVITY_PAUSED else "start"
        await self._send(command)

    async def async_pause(self) -> None:
        await self._send("pause")

    async def async_stop(self, **kwargs: Any) -> None:
        await self._send("stop")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        # The robot ignores "dock" while running a mission; pause it first.
        if self.activity == ACTIVITY_CLEANING:
            await self._send("pause")
            await asyncio.sleep(1)
        await self._send("dock")

    async def async_locate(self, **kwargs: Any) -> None:
        await self._send("find")

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if params is not None and not isinstance(params, dict):
            raise HomeAssistantError("params must be a mapping")
        await self._send(command, **(params or {}))

    async def _send(self, command: str, **params: Any) -> None:
        try:
            await self.hass.async_add_executor_job(
                lambda: self.client.send_command(command, **params)
            )
        except RoombaError as err:
            raise HomeAssistantError(f"Roomba command {command} failed: {err}") from err
