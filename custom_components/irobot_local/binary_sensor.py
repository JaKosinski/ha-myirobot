"""Binary sensors for iRobot Local."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IRobotConfigEntry
from .client import RoombaClient
from .entity import RoombaEntity


def _bin(state: dict[str, Any], key: str) -> Any:
    return (state.get("bin") or {}).get(key)


@dataclass(frozen=True, kw_only=True)
class RoombaBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS = (
    RoombaBinarySensorDescription(
        key="bin_full",
        translation_key="bin_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: _bin(s, "full"),
    ),
    RoombaBinarySensorDescription(
        key="bin_missing",
        translation_key="bin_missing",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: None if _bin(s, "present") is None else not _bin(s, "present"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRobotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        RoombaBinarySensor(entry.runtime_data, description)
        for description in BINARY_SENSORS
    )


class RoombaBinarySensor(RoombaEntity, BinarySensorEntity):
    entity_description: RoombaBinarySensorDescription

    def __init__(
        self, client: RoombaClient, description: RoombaBinarySensorDescription
    ) -> None:
        super().__init__(client, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.client.state)
