"""Setting up the integration with a mocked robot connection."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.irobot_local.client import (
    RoombaAuthError,
    RoombaClient,
    RoombaConnectionError,
)
from custom_components.irobot_local.const import DOMAIN

BLID = "0123456789ABCDEF0123456789ABCDEF"
DATA = {"host": "192.0.2.10", "blid": BLID, "password": ":1:1600000000:testpassword"}
STATE = {
    "name": "Roomba",
    "sku": "i755840",
    "softwareVer": "lewis+3.2.0",
    "batPct": 87,
    "bin": {"present": True, "full": False},
    "cleanMissionStatus": {"cycle": "clean", "phase": "run", "error": 0, "mssnM": 12, "sqft": 100},
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def _connect(self, timeout=10):
    self.state.update(STATE)
    self.connected = True


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=BLID, data=DATA, options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_entities(hass: HomeAssistant) -> None:
    with (
        patch.object(RoombaClient, "connect", _connect),
        patch.object(RoombaClient, "wait_for", return_value=True),
        patch.object(RoombaClient, "disconnect"),
        patch.object(RoombaClient, "send_command") as send,
    ):
        entry = await _setup(hass)
        assert entry.state is ConfigEntryState.LOADED

        assert hass.states.get("vacuum.roomba").state == "cleaning"
        assert hass.states.get("sensor.roomba_battery").state == "87"
        assert hass.states.get("binary_sensor.roomba_bin_full").state == "off"

        await hass.services.async_call(
            "vacuum", "return_to_base", {"entity_id": "vacuum.roomba"}, blocking=True
        )
        assert [call.args[0] for call in send.call_args_list] == ["pause", "dock"]

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_setup_clean_area_with_rooms(hass: HomeAssistant) -> None:
    rooms = [{"id": "5", "type": "rid", "name": "Kitchen"}]
    with (
        patch.object(RoombaClient, "connect", _connect),
        patch.object(RoombaClient, "wait_for", return_value=True),
        patch.object(RoombaClient, "disconnect"),
    ):
        await _setup(hass, pmap_id="abc", user_pmapv_id="v1", rooms=rooms)
    features = hass.states.get("vacuum.roomba").attributes["supported_features"]
    assert features & 16384  # VacuumEntityFeature.CLEAN_AREA


async def test_setup_retries_when_unreachable(hass: HomeAssistant) -> None:
    with patch.object(RoombaClient, "connect", side_effect=RoombaConnectionError("down")):
        entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_starts_reauth_on_bad_password(hass: HomeAssistant) -> None:
    with patch.object(RoombaClient, "connect", side_effect=RoombaAuthError("bad")):
        entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
