"""Base entity for iRobot Local."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .client import RoombaClient
from .const import DOMAIN, signal_update


class RoombaEntity(Entity):
    """Push-updated entity bound to one robot."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client: RoombaClient, key: str | None = None) -> None:
        self.client = client
        self._attr_unique_id = f"{client.blid}_{key}" if key else client.blid
        state = client.state
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.blid)},
            manufacturer="iRobot",
            name=state.get("name") or "Roomba",
            model=state.get("sku"),
            sw_version=state.get("softwareVer"),
        )

    @property
    def available(self) -> bool:
        return self.client.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self.client.blid), self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
