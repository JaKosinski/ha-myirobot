"""Sensors for iRobot Local."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IRobotConfigEntry
from .client import RoombaClient
from .const import ERROR_MESSAGES
from .entity import RoombaEntity


def _mission(state: dict[str, Any], key: str) -> Any:
    return (state.get("cleanMissionStatus") or {}).get(key)


@dataclass(frozen=True, kw_only=True)
class RoombaSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


SENSORS = (
    RoombaSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.get("batPct"),
    ),
    RoombaSensorDescription(
        key="phase",
        translation_key="phase",
        value_fn=lambda s: _mission(s, "phase"),
    ),
    RoombaSensorDescription(
        key="error",
        translation_key="error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _mission(s, "error"),
    ),
    RoombaSensorDescription(
        key="mission_duration",
        translation_key="mission_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda s: _mission(s, "mssnM"),
    ),
    RoombaSensorDescription(
        key="cleaned_area",
        translation_key="cleaned_area",
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_FEET,
        value_fn=lambda s: _mission(s, "sqft"),
    ),
    RoombaSensorDescription(
        key="missions_total",
        translation_key="missions_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: _mission(s, "nMssn"),
    ),
    RoombaSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: (s.get("signal") or {}).get("rssi"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRobotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        RoombaSensor(entry.runtime_data, description) for description in SENSORS
    )


class RoombaSensor(RoombaEntity, SensorEntity):
    entity_description: RoombaSensorDescription

    def __init__(
        self, client: RoombaClient, description: RoombaSensorDescription
    ) -> None:
        super().__init__(client, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.client.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "error":
            return None
        code = self.native_value
        if code is None:
            return None
        return {"message": ERROR_MESSAGES.get(code, f"Unknown error {code}")}
