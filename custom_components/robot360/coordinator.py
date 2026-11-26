"""Coordinator: login, device list, status polling and the push channel."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_TOKENS,
    CONSUMABLES_INTERVAL,
    DOMAIN,
    INFO_CONSUMABLES,
    NON_STATUS_INFO_TYPES,
    SESSION_MAX_AGE,
    STATUS_DELAY_AFTER_COMMAND,
    UPDATE_INTERVAL,
)
from .lib360 import (
    PushClient,
    PushMessage,
    Robot360Api,
    Robot360AuthError,
    Robot360CaptchaRequired,
    Robot360Error,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class RobotState:
    sn: str
    info: dict[str, Any] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    # Operating seconds of battery, filter, brushes, sensors (answer to 21015).
    consumables: dict[str, Any] = field(default_factory=dict)
    last_status: Any = None  # datetime of the last status push
    consumables_requested: float = 0.0
    messages: deque = field(default_factory=lambda: deque(maxlen=15))

    @property
    def name(self) -> str:
        return str(self.info.get("title") or self.info.get("name") or f"360 {self.sn[-4:]}")


type Robot360ConfigEntry = ConfigEntry[Robot360Coordinator]


class Robot360Coordinator(DataUpdateCoordinator[dict[str, RobotState]]):
    config_entry: Robot360ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: Robot360ConfigEntry, api: Robot360Api) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self.push: PushClient | None = None
        self.robots: dict[str, RobotState] = {}
        self._pending_refresh: dict[str, Callable[[], None]] = {}

    # ------------------------------------------------------------------ session

    async def _async_login(self) -> None:
        try:
            await self.api.async_connect()
        except Robot360CaptchaRequired as err:
            raise ConfigEntryAuthFailed(
                "360 asks for a captcha. Re-enter your password in the integration to solve it."
            ) from err
        except Robot360AuthError as err:
            raise ConfigEntryAuthFailed(f"360 login failed: {err}") from err
        except (Robot360Error, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"360 login failed: {err}") from err

        if self.push is not None:
            self.push.failures = 0
        self._sync_push()
        self._save_tokens()

    def _save_tokens(self) -> None:
        """Keep the latest account login in the config entry."""
        tokens = self.api.tokens.as_dict() if self.api.tokens else None
        if tokens and tokens != self.config_entry.data.get(CONF_TOKENS):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_TOKENS: tokens}
            )

    def _sync_push(self) -> None:
        """Start the push channel, or point it at the current sid/pushKey."""
        session = self.api.session
        if session is None:
            return
        if self.push is None:
            self.push = PushClient(session.sid, session.push_key, self._handle_push)
            self.push.start()
        else:
            self.push.update_session(session.sid, session.push_key)

    def _session_needs_refresh(self) -> bool:
        session = self.api.session
        if session is None:
            return True
        if time.time() - session.created > SESSION_MAX_AGE.total_seconds():
            return True
        return self.push is not None and self.push.failures >= 3

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        for cancel in self._pending_refresh.values():
            cancel()
        self._pending_refresh.clear()
        if self.push is not None:
            await self.push.stop()
            self.push = None

    # ------------------------------------------------------------------ polling

    async def _async_update_data(self) -> dict[str, RobotState]:
        if self._session_needs_refresh():
            await self._async_login()

        try:
            devices = await self.api.async_get_devices()
        except Robot360AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Robot360CaptchaRequired as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (Robot360Error, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Device list failed: {err}") from err

        for dev in devices:
            sn = str(dev.get("sn") or dev.get("devSn") or dev.get("deviceSn") or "")
            if not sn:
                continue
            self.robots.setdefault(sn, RobotState(sn)).info = dev

        # The answers arrive on the push channel.
        now = time.time()
        for sn, robot in self.robots.items():
            try:
                await self.api.async_request_status(sn)
                if now - robot.consumables_requested > CONSUMABLES_INTERVAL.total_seconds():
                    robot.consumables_requested = now
                    await self.api.async_request_consumables(sn)
            except (Robot360Error, aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug("Status request for %s failed: %s", sn, err)

        # api may have logged in again on its own (errno 102/103).
        self._sync_push()
        self._save_tokens()

        return self.robots

    # ------------------------------------------------------------------ push

    @callback
    def _handle_push(self, msg: PushMessage) -> None:
        robot = self.robots.get(msg.sn)
        if robot is None:
            _LOGGER.debug("Push for unknown robot %s", msg.sn)
            return
        robot.messages.append(
            {"time": dt_util.utcnow().isoformat(), "infoType": msg.info_type, "data": msg.data}
        )
        _LOGGER.debug("Push %s infoType=%s data=%s", msg.sn, msg.info_type, str(msg.data)[:500])
        if msg.info_type in NON_STATUS_INFO_TYPES or not isinstance(msg.data, dict):
            return
        if msg.info_type == INFO_CONSUMABLES:
            robot.consumables.update(msg.data)
        elif "map" in msg.data or "pathData" in msg.data:
            return
        else:
            robot.status.update(msg.data)
            robot.last_status = dt_util.utcnow()
        # Not async_set_updated_data: that would keep resetting the poll timer.
        self.async_update_listeners()

    # ------------------------------------------------------------------ commands

    async def async_command(self, sn: str, method: str, *args: Any) -> None:
        """Run an api command, then ask for fresh status shortly after."""
        try:
            await getattr(self.api, method)(sn, *args)
        except Robot360AuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise Robot360Error(str(err)) from err

        async def _refresh(_now: Any) -> None:
            self._pending_refresh.pop(sn, None)
            try:
                await self.api.async_request_status(sn)
            except (Robot360Error, aiohttp.ClientError, TimeoutError):
                pass

        if cancel := self._pending_refresh.pop(sn, None):
            cancel()
        self._pending_refresh[sn] = async_call_later(self.hass, STATUS_DELAY_AFTER_COMMAND, _refresh)
