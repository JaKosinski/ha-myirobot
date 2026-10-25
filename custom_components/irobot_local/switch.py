"""Switch that records raw MQTT traffic for reverse engineering."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IRobotConfigEntry
from .client import RoombaClient
from .const import DOMAIN
from .entity import RoombaEntity
from .recorder import MessageRecorder


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IRobotConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([RecordMessagesSwitch(entry.runtime_data)])


class RecordMessagesSwitch(RoombaEntity, SwitchEntity):
    """Writes every robot message to /config/irobot_local/<blid>_<time>.jsonl."""

    _attr_translation_key = "record_messages"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client: RoombaClient) -> None:
        super().__init__(client, "record_messages")
        self._recorder = MessageRecorder()

    @property
    def available(self) -> bool:
        # Keep the switch usable while the robot reconnects.
        return True

    @property
    def is_on(self) -> bool:
        return self._recorder.recording

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "file": str(self._recorder.path) if self._recorder.path else None,
            "messages": self._recorder.messages,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.client.add_raw_listener(self._recorder.handle))
        self.async_on_remove(self._stop_on_remove)

    def _stop_on_remove(self) -> None:
        self.hass.async_add_executor_job(self._recorder.stop)

    async def async_turn_on(self, **kwargs: Any) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path(self.hass.config.path(DOMAIN, f"{self.client.blid}_{stamp}.jsonl"))
        await self.hass.async_add_executor_job(self._recorder.start, path)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hass.async_add_executor_job(self._recorder.stop)
        self.async_write_ha_state()
