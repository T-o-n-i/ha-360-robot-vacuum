"""Base entity."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import Robot360Coordinator, RobotState


class Robot360Entity(CoordinatorEntity[Robot360Coordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: Robot360Coordinator, sn: str, key: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_{key}"
        info = self.robot.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            name=self.robot.name,
            manufacturer=MANUFACTURER,
            model=str(info.get("hardware") or info.get("model") or "S6"),
            serial_number=sn,
            sw_version=_first_str(info, "version", "firmwareVer", "softVer", "romVersion"),
        )

    @property
    def robot(self) -> RobotState:
        return self.coordinator.robots[self._sn]

    @property
    def status(self) -> dict[str, Any]:
        return self.robot.status

    @property
    def available(self) -> bool:
        if not super().available or self._sn not in self.coordinator.robots:
            return False
        online = self.robot.info.get("online")
        return online is None or str(online) not in ("0", "false", "False")


def _first_str(info: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if info.get(key):
            return str(info[key])
    return None
