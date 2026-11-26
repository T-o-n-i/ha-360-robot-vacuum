"""Volume of the voice prompts."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Robot360ConfigEntry, Robot360Coordinator
from .entity import Robot360Entity
from .lib360 import Robot360Error
from .lib360 import status as st


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Robot360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(Robot360Volume(coordinator, sn) for sn in coordinator.robots)


class Robot360Volume(Robot360Entity, NumberEntity):
    _attr_translation_key = "volume"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: Robot360Coordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "volume")

    @property
    def native_value(self) -> int | None:
        return st.volume(self.status)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.async_command(self._sn, "async_set_volume", int(value))
        except Robot360Error as err:
            raise HomeAssistantError(f"360 command failed: {err}") from err
        # Show the new value right away; the next status push confirms it.
        self.robot.status["vol"] = int(value)
        self.async_write_ha_state()
