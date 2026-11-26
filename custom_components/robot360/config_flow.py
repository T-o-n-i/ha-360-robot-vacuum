"""Config flow: login (e-mail or mobile number) + password of the 360Robot app account."""

from __future__ import annotations

import base64
import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ANDROIDID, CONF_MID, CONF_TOKENS, DOMAIN
from .lib360 import (
    DeviceIdentity,
    Robot360Api,
    Robot360AuthError,
    Robot360CaptchaRequired,
    Robot360Error,
)

_LOGGER = logging.getLogger(__name__)

CONF_CAPTCHA = "captcha"


class Robot360ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._identity = DeviceIdentity()
        self._captcha_sc = ""
        self._captcha_image = ""
        self._tokens: dict[str, str] | None = None

    def _api(self) -> Robot360Api:
        return Robot360Api(async_get_clientsession(self.hass), self._username, self._password, self._identity)

    async def _try_login(self, captcha: str | None = None) -> tuple[str | None, str | None]:
        """Return (error_key, qid)."""
        api = self._api()
        try:
            session = await api.async_login(self._captcha_sc or None, captcha)
            devices = await api.async_get_devices()
        except Robot360CaptchaRequired:
            return "captcha", None
        except Robot360AuthError as err:
            _LOGGER.debug("Login rejected: %s (errno %s)", err, err.errno)
            return "invalid_auth", None
        except (Robot360Error, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("360 login failed: %s", err)
            return "cannot_connect", None
        if not devices:
            return "no_devices", None
        self._tokens = api.tokens.as_dict() if api.tokens else None
        return None, session.qid

    async def _load_captcha(self) -> None:
        captcha = await self._api().async_get_captcha()
        self._captcha_sc = captcha.sc
        self._captcha_image = base64.b64encode(captcha.image).decode("ascii")

    def _entry_data(self) -> dict[str, str]:
        return {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            **self._identity.as_dict(),
            CONF_TOKENS: self._tokens,
        }

    async def _finish(self, qid: str) -> ConfigFlowResult:
        await self.async_set_unique_id(qid)
        if self.source == "reauth":
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=self._entry_data())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._username, data=self._entry_data())

    async def _handle_credentials(self) -> tuple[ConfigFlowResult | None, dict[str, str]]:
        """Try to log in. Returns (next flow step, errors for the current form)."""
        error, qid = await self._try_login()
        if error == "captcha":
            try:
                await self._load_captcha()
            except (Robot360Error, aiohttp.ClientError, TimeoutError):
                return None, {"base": "cannot_connect"}
            return await self.async_step_captcha(), {}
        if error:
            return None, {"base": error}
        return await self._finish(qid), {}  # type: ignore[arg-type]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            result, errors = await self._handle_credentials()
            if result is not None:
                return result
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_USERNAME, default=self._username): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    async def async_step_captcha(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error, qid = await self._try_login(user_input[CONF_CAPTCHA].strip())
            if error is None:
                return await self._finish(qid)  # type: ignore[arg-type]
            if error != "captcha":
                errors["base"] = error
            else:
                errors["base"] = "captcha_wrong"
            try:
                await self._load_captcha()
            except (Robot360Error, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema({vol.Required(CONF_CAPTCHA): str}),
            description_placeholders={"captcha": f"![captcha](data:image/png;base64,{self._captcha_image})"},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._username = entry_data[CONF_USERNAME]
        self._identity = DeviceIdentity(mid=entry_data[CONF_MID], androidid=entry_data[CONF_ANDROIDID])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            result, errors = await self._handle_credentials()
            if result is not None:
                return result
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": self._username},
            errors=errors,
        )
