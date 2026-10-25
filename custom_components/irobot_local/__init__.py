"""iRobot Local: local-only Roomba integration over MQTT/TLS."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client import RoombaAuthError, RoombaClient, RoombaConnectionError
from .const import CONF_BLID, STATE_WAIT_TIMEOUT, signal_update

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VACUUM,
]

type IRobotConfigEntry = ConfigEntry[RoombaClient]


async def async_setup_entry(hass: HomeAssistant, entry: IRobotConfigEntry) -> bool:
    """Connect to the robot and set up platforms."""
    client = RoombaClient(
        entry.data[CONF_HOST], entry.data[CONF_BLID], entry.data[CONF_PASSWORD]
    )
    signal = signal_update(client.blid)
    remove_listener = client.add_listener(
        lambda: hass.loop.call_soon_threadsafe(async_dispatcher_send, hass, signal)
    )

    try:
        await hass.async_add_executor_job(client.connect)
    except RoombaAuthError as err:
        remove_listener()
        raise ConfigEntryAuthFailed(f"Robot rejected BLID/password: {err}") from err
    except RoombaConnectionError as err:
        remove_listener()
        raise ConfigEntryNotReady(f"Cannot connect to {client.host}: {err}") from err

    # Name, SKU and firmware feed the device registry, give them a moment.
    await hass.async_add_executor_job(
        client.wait_for, ("name", "sku", "cleanMissionStatus"), STATE_WAIT_TIMEOUT
    )

    entry.runtime_data = client
    entry.async_on_unload(remove_listener)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def _disconnect_on_stop(_event: Event) -> None:
        await hass.async_add_executor_job(client.disconnect)

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _disconnect_on_stop)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: IRobotConfigEntry) -> None:
    """Rooms changed: reload so the vacuum reports the new segments."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: IRobotConfigEntry) -> bool:
    """Unload platforms and drop the robot connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await hass.async_add_executor_job(entry.runtime_data.disconnect)
    return unloaded
