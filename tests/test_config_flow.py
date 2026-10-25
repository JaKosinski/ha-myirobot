"""Config flow tests inside a real Home Assistant instance."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.irobot_local.auth import (
    CloudAuthError,
    LocalPasswordError,
    RobotCredentials,
)
from custom_components.irobot_local.client import RoombaAuthError, RoombaClient
from custom_components.irobot_local.const import DOMAIN

FLOW = "custom_components.irobot_local.config_flow"
HOST = "192.0.2.10"
BLID = "0123456789ABCDEF0123456789ABCDEF"
PASSWORD = ":1:1600000000:testpassword"
EXPECTED = {"host": HOST, "blid": BLID, "password": PASSWORD}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def robot_ok():
    """The robot accepts any credentials and reports its name."""

    def connect(self, timeout=10):
        self.state["name"] = "Roomba"

    with (
        patch.object(RoombaClient, "connect", connect),
        patch.object(RoombaClient, "wait_for", return_value=True),
        patch.object(RoombaClient, "disconnect"),
        patch("custom_components.irobot_local.async_setup_entry", return_value=True),
    ):
        yield


async def _start(hass: HomeAssistant, method: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": method}
    )


async def test_local(hass: HomeAssistant, robot_ok) -> None:
    result = await _start(hass, "local")
    assert result["step_id"] == "local"
    with (
        patch(f"{FLOW}.get_robot_info", return_value={"blid": BLID}),
        patch(f"{FLOW}.get_password_local", return_value=PASSWORD),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": HOST}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Roomba"
    assert result["data"] == EXPECTED


async def test_local_fails_then_cloud(hass: HomeAssistant, robot_ok) -> None:
    result = await _start(hass, "local")
    with (
        patch(f"{FLOW}.get_robot_info", return_value={"blid": BLID}),
        patch(f"{FLOW}.get_password_local", side_effect=LocalPasswordError("not pairing")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": HOST}
        )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "local_failed"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    assert result["step_id"] == "cloud"
    robots = [
        RobotCredentials("OTHERBLID", "other"),
        RobotCredentials(BLID, PASSWORD, "Roomba"),
    ]
    with patch(f"{FLOW}.get_credentials_cloud", AsyncMock(return_value=robots)) as cloud:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "a@b.pl", "password": "secret", "country": "pl", "host": HOST},
        )
    # The BLID read locally picks the right robot from the account.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == EXPECTED
    assert cloud.call_args.args[1:] == ("a@b.pl", "secret", "PL")


async def test_cloud_pick_robot(hass: HomeAssistant, robot_ok) -> None:
    result = await _start(hass, "cloud")
    robots = [RobotCredentials("OTHERBLID", "other", "Braava"), RobotCredentials(BLID, PASSWORD, "Roomba")]
    with patch(f"{FLOW}.get_credentials_cloud", AsyncMock(return_value=robots)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "a@b.pl", "password": "secret", "country": "PL", "host": HOST},
        )
    assert result["step_id"] == "pick_robot"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"blid": BLID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == EXPECTED


async def test_cloud_invalid_auth(hass: HomeAssistant) -> None:
    result = await _start(hass, "cloud")
    with patch(f"{FLOW}.get_credentials_cloud", AsyncMock(side_effect=CloudAuthError)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "a@b.pl", "password": "bad", "country": "PL", "host": HOST},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_cloud_auth"}


async def test_manual_strips_and_validates(hass: HomeAssistant, robot_ok) -> None:
    result = await _start(hass, "manual")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": f" {HOST}", "blid": BLID, "password": f"{PASSWORD} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == EXPECTED


async def test_manual_invalid_auth(hass: HomeAssistant) -> None:
    result = await _start(hass, "manual")
    with (
        patch.object(RoombaClient, "connect", side_effect=RoombaAuthError("bad")),
        patch.object(RoombaClient, "disconnect"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": HOST, "blid": BLID, "password": "x"}
        )
    assert result["errors"] == {"base": "invalid_auth"}


def _entry(hass: HomeAssistant):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, unique_id=BLID, data=dict(EXPECTED))
    entry.add_to_hass(hass)
    return entry


async def test_dhcp_updates_ip_of_known_robot(hass: HomeAssistant) -> None:
    from custom_components.irobot_local.config_flow import DhcpServiceInfo

    entry = _entry(hass)
    with patch("custom_components.irobot_local.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=DhcpServiceInfo(
                ip="192.0.2.99", hostname=f"irobot-{BLID.lower()}", macaddress="501479000000"
            ),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "192.0.2.99"


async def test_dhcp_new_robot_prefills_host(hass: HomeAssistant) -> None:
    from custom_components.irobot_local.config_flow import DhcpServiceInfo

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip="192.0.2.50", hostname="roomba-abcdef", macaddress="501479000001"),
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    schema = result["data_schema"].schema
    suggested = {str(key): key.description["suggested_value"] for key in schema if key.description}
    assert suggested["host"] == "192.0.2.50"
    assert suggested["blid"] == "ABCDEF"


async def test_reauth(hass: HomeAssistant, robot_ok) -> None:
    entry = _entry(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": " :1:2:new "}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == ":1:2:new"


async def test_reconfigure(hass: HomeAssistant, robot_ok) -> None:
    entry = _entry(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.0.2.20", "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {**EXPECTED, "host": "192.0.2.20"}
