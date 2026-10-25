"""Config flow for iRobot Local."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_COUNTRY, CONF_EMAIL, CONF_HOST, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .auth import (
    CloudAuthError,
    CloudError,
    LocalPasswordError,
    RobotCredentials,
    get_credentials_cloud,
    get_password_local,
    get_robot_info,
)
from .client import RoombaAuthError, RoombaClient, RoombaConnectionError

try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:  # Home Assistant before 2025.1
    from homeassistant.components.dhcp import DhcpServiceInfo
from .const import (
    CONF_BLID,
    CONF_PMAP_ID,
    CONF_ROOMS,
    CONF_USER_PMAPV_ID,
    DOMAIN,
    STATE_WAIT_TIMEOUT,
)
from .rooms import RoomsParseError, format_rooms, parse_rooms, suggest_from_last_command

_LOGGER = logging.getLogger(__name__)

HOST_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_BLID): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

CLOUD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
        vol.Required(CONF_COUNTRY): str,
        vol.Required(CONF_HOST): str,
    }
)


class IRobotLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Get BLID and robot password locally, from the iRobot cloud or by hand.

    Whatever the source, the robot is then controlled only locally and only
    the robot password is stored, never the iRobot account credentials.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._blid: str | None = None
        self._robots: dict[str, RobotCredentials] = {}
        self._name: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> IRobotLocalOptionsFlow:
        return IRobotLocalOptionsFlow()

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Robot seen on the network: update its IP or offer to add it."""
        # Hostname is "iRobot-<BLID>" (i/s/j series) or "Roomba-<BLID>".
        blid = discovery_info.hostname.partition("-")[2].upper()
        if not blid:
            return self.async_abort(reason="not_irobot_device")
        await self.async_set_unique_id(blid)
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})
        self._host = discovery_info.ip
        self._blid = blid
        self.context["title_placeholders"] = {"name": discovery_info.hostname}
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="user", menu_options=["local", "cloud", "manual"]
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read BLID over UDP and the password from a robot in pairing mode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            try:
                info = await self.hass.async_add_executor_job(
                    get_robot_info, self._host
                )
                self._blid = info["blid"]
                password = await self.hass.async_add_executor_job(
                    get_password_local, self._host
                )
            except LocalPasswordError as err:
                _LOGGER.info("Local password retrieval failed: %s", err)
                return await self.async_step_local_failed()

            if error := await self._async_check(self._host, self._blid, password):
                errors["base"] = error
            else:
                return self._create(self._host, self._blid, password)

        return self.async_show_form(
            step_id="local",
            data_schema=self.add_suggested_values_to_schema(
                HOST_SCHEMA, user_input or {CONF_HOST: self._host or ""}
            ),
            errors=errors,
        )

    async def async_step_local_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="local_failed", menu_options=["local", "cloud", "manual"]
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch BLID and robot password from the iRobot account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            try:
                robots = await get_credentials_cloud(
                    async_get_clientsession(self.hass),
                    user_input[CONF_EMAIL].strip(),
                    user_input[CONF_PASSWORD],
                    user_input[CONF_COUNTRY].strip().upper(),
                )
            except CloudAuthError:
                errors["base"] = "invalid_cloud_auth"
            except CloudError as err:
                _LOGGER.warning("iRobot cloud error: %s", err)
                errors["base"] = "cloud_error"
            else:
                self._robots = {robot.blid: robot for robot in robots}
                if not robots:
                    errors["base"] = "no_robots"
                elif self._blid in self._robots or len(robots) == 1:
                    robot = self._robots.get(self._blid or "") or robots[0]
                    if result := await self._async_try_robot(robot, errors):
                        return result
                else:
                    return await self.async_step_pick_robot()

        suggested = {
            CONF_COUNTRY: self.hass.config.country or "US",
            CONF_HOST: self._host or "",
        }
        if user_input:
            suggested |= {
                key: value for key, value in user_input.items() if key != CONF_PASSWORD
            }
        return self.async_show_form(
            step_id="cloud",
            data_schema=self.add_suggested_values_to_schema(CLOUD_SCHEMA, suggested),
            errors=errors,
        )

    async def async_step_pick_robot(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            robot = self._robots[user_input[CONF_BLID]]
            if result := await self._async_try_robot(robot, errors):
                return result

        options = [
            SelectOptionDict(
                value=robot.blid,
                label=f"{robot.name or 'Roomba'} ({robot.sku or robot.blid})",
            )
            for robot in self._robots.values()
        ]
        return self.async_show_form(
            step_id="pick_robot",
            data_schema=vol.Schema(
                {vol.Required(CONF_BLID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Copy-paste often adds whitespace; robot passwords never contain any.
            data = {key: value.strip() for key, value in user_input.items()}
            if error := await self._async_check(
                data[CONF_HOST], data[CONF_BLID], data[CONF_PASSWORD]
            ):
                errors["base"] = error
            else:
                return self._create(data[CONF_HOST], data[CONF_BLID], data[CONF_PASSWORD])

        suggested = user_input or {CONF_HOST: self._host or "", CONF_BLID: self._blid or ""}
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(MANUAL_SCHEMA, suggested),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Robot rejected the stored password (e.g. after a factory reset)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input[CONF_PASSWORD].strip()
            if error := await self._async_check(
                entry.data[CONF_HOST], entry.data[CONF_BLID], password, new_entry=False
            ):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: password}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the robot IP or password without removing the integration."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {key: value.strip() for key, value in user_input.items()}
            if error := await self._async_check(
                data[CONF_HOST], entry.data[CONF_BLID], data[CONF_PASSWORD], new_entry=False
            ):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {vol.Required(CONF_HOST): str, vol.Required(CONF_PASSWORD): str}
                ),
                user_input or entry.data,
            ),
            errors=errors,
        )

    async def _async_try_robot(
        self, robot: RobotCredentials, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Validate cloud credentials locally; None (and errors set) on failure."""
        assert self._host is not None
        if error := await self._async_check(self._host, robot.blid, robot.password):
            errors["base"] = error
            return None
        return self._create(self._host, robot.blid, robot.password, robot.name)

    async def _async_check(
        self, host: str, blid: str, password: str, new_entry: bool = True
    ) -> str | None:
        """Connect once to validate; returns an error key or None."""
        if new_entry:
            await self.async_set_unique_id(blid.upper())
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        client = RoombaClient(host, blid, password)
        try:
            await self.hass.async_add_executor_job(client.connect)
            await self.hass.async_add_executor_job(
                client.wait_for, ("name",), STATE_WAIT_TIMEOUT
            )
        except RoombaAuthError:
            return "invalid_auth"
        except RoombaConnectionError as err:
            _LOGGER.warning("Cannot connect to %s: %s", host, err)
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error connecting to the robot")
            return "unknown"
        else:
            self._name = client.state.get("name")
            return None
        finally:
            # Free the robot's single MQTT slot for the config entry.
            await self.hass.async_add_executor_job(client.disconnect)

    def _create(
        self, host: str, blid: str, password: str, name: str | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(
            title=self._name or name or "Roomba",
            data={CONF_HOST: host, CONF_BLID: blid, CONF_PASSWORD: password},
        )


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PMAP_ID): str,
        vol.Optional(CONF_USER_PMAPV_ID): str,
        vol.Optional(CONF_ROOMS): TextSelector(TextSelectorConfig(multiline=True)),
    }
)


class IRobotLocalOptionsFlow(OptionsFlow):
    """Name the Smart Map regions so they can be mapped to Home Assistant areas."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            pmap_id = user_input.get(CONF_PMAP_ID, "").strip()
            try:
                rooms = parse_rooms(user_input.get(CONF_ROOMS, ""))
            except RoomsParseError as err:
                errors[CONF_ROOMS] = "invalid_rooms"
                placeholders["error"] = str(err)
            else:
                if rooms and not pmap_id:
                    errors[CONF_PMAP_ID] = "pmap_required"
                else:
                    return self.async_create_entry(
                        data={
                            CONF_PMAP_ID: pmap_id,
                            CONF_USER_PMAPV_ID: user_input.get(
                                CONF_USER_PMAPV_ID, ""
                            ).strip(),
                            CONF_ROOMS: rooms,
                        }
                    )

        if user_input is None:
            options = self.config_entry.options
            suggested = {
                CONF_PMAP_ID: options.get(CONF_PMAP_ID, ""),
                CONF_USER_PMAPV_ID: options.get(CONF_USER_PMAPV_ID, ""),
                CONF_ROOMS: format_rooms(options.get(CONF_ROOMS, [])),
            }
            if not options.get(CONF_ROOMS):
                client = getattr(self.config_entry, "runtime_data", None)
                if isinstance(client, RoombaClient):
                    suggested |= suggest_from_last_command(
                        client.state.get("lastCommand")
                    )
        else:
            suggested = user_input

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, suggested),
            errors=errors,
            description_placeholders=placeholders,
        )
