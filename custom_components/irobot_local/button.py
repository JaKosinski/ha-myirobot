"""Buttons for iRobot Local."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IRobotConfigEntry
from .client import RoombaClient, RoombaError
from .entity import RoombaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRobotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    client = entry.runtime_data
    async_add_entities(
        [
            # Empties the bin into the Clean Base (i7+ only).
            CommandButton(client, "empty_bin", "evac"),
            # Mapping run: explores without cleaning to build the Smart Map.
            CommandButton(client, "mapping_run", "train"),
        ]
    )


class CommandButton(RoombaEntity, ButtonEntity):
    """Sends one mission command."""

    def __init__(self, client: RoombaClient, key: str, command: str) -> None:
        super().__init__(client, key)
        self._attr_translation_key = key
        self._command = command

    async def async_press(self) -> None:
        try:
            await self.hass.async_add_executor_job(
                self.client.send_command, self._command
            )
        except RoombaError as err:
            raise HomeAssistantError(
                f"Roomba command {self._command} failed: {err}"
            ) from err
