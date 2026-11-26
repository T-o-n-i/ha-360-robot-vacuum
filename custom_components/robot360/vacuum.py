"""Vacuum entity."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Robot360ConfigEntry, Robot360Coordinator
from .entity import Robot360Entity
from .lib360 import Robot360Error
from .lib360 import status as st

ACTIVITY = {
    "docked": VacuumActivity.DOCKED,
    "cleaning": VacuumActivity.CLEANING,
    "paused": VacuumActivity.PAUSED,
    "idle": VacuumActivity.IDLE,
    "returning": VacuumActivity.RETURNING,
    "error": VacuumActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Robot360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(Robot360Vacuum(coordinator, sn) for sn in coordinator.robots)


class Robot360Vacuum(Robot360Entity, StateVacuumEntity):
    _attr_name = None
    _attr_translation_key = "robot"
    _attr_fan_speed_list = st.FAN_SPEEDS
    _attr_supported_features = (
        VacuumEntityFeature.START
        | VacuumEntityFeature.PAUSE
        | VacuumEntityFeature.STOP
        | VacuumEntityFeature.RETURN_HOME
        | VacuumEntityFeature.FAN_SPEED
        | VacuumEntityFeature.LOCATE
        | VacuumEntityFeature.STATE
        | VacuumEntityFeature.SEND_COMMAND
    )

    def __init__(self, coordinator: Robot360Coordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "vacuum")

    @property
    def activity(self) -> VacuumActivity | None:
        if st.error_code(self.status):
            return VacuumActivity.ERROR
        name = st.activity(self.status)
        return ACTIVITY.get(name) if name else None

    @property
    def fan_speed(self) -> str | None:
        return st.fan_speed(self.status)

    async def _cmd(self, method: str, *args: Any) -> None:
        try:
            await self.coordinator.async_command(self._sn, method, *args)
        except Robot360Error as err:
            raise HomeAssistantError(f"360 command failed: {err}") from err

    async def async_start(self) -> None:
        if self.activity == VacuumActivity.PAUSED:
            await self._cmd("async_resume")
        else:
            await self._cmd("async_start")

    async def async_pause(self) -> None:
        await self._cmd("async_pause")

    async def async_stop(self, **kwargs: Any) -> None:
        # The 360 cloud has no separate "stop"; pausing leaves the robot where it is.
        await self._cmd("async_pause")

    async def async_return_to_base(self, **kwargs: Any) -> None:
        await self._cmd("async_return_to_base")

    async def async_locate(self, **kwargs: Any) -> None:
        await self._cmd("async_locate")

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        if fan_speed not in st.FAN_SPEEDS:
            raise HomeAssistantError(f"Unknown fan speed {fan_speed}, use one of {st.FAN_SPEEDS}")
        await self._cmd("async_set_fan_speed", fan_speed)

    async def async_send_command(
        self, command: str, params: dict[str, Any] | list[Any] | None = None, **kwargs: Any
    ) -> None:
        """Raw command: ``command`` is the infoType (e.g. "21024"), ``params`` the data object."""
        data: Any = params if params is not None else ""
        if isinstance(data, list):
            data = json.dumps(data)
        await self._cmd("async_send_command", command, data)
