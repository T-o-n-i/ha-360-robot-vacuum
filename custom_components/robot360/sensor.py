"""Sensors: battery, status, error, last update."""

from __future__ import annotations

import re

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import MATCH_ALL, PERCENTAGE, EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Robot360ConfigEntry, Robot360Coordinator, RobotState
from .entity import Robot360Entity
from .lib360 import status as st


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


@dataclass(frozen=True, kw_only=True)
class Robot360SensorDescription(SensorEntityDescription):
    value_fn: Callable[[RobotState], Any]


SENSORS: tuple[Robot360SensorDescription, ...] = (
    Robot360SensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda r: st.battery(r.status),
    ),
    Robot360SensorDescription(
        key="mode",
        translation_key="mode",
        value_fn=lambda r: st.mode(r.status),
    ),
    Robot360SensorDescription(
        key="error",
        translation_key="error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: st.error_code(r.status),
    ),
    Robot360SensorDescription(
        key="clean_area",
        translation_key="clean_area",
        device_class=SensorDeviceClass.AREA,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        value_fn=lambda r: st.clean_area(r.status),
    ),
    Robot360SensorDescription(
        key="clean_time",
        translation_key="clean_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda r: st.clean_minutes(r.status),
    ),
    *(
        Robot360SensorDescription(
            key=f"{part}_hours",
            # unique_id keeps the camelCase key; translation keys must be snake_case.
            translation_key=f"{_snake(part)}_hours",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.HOURS,
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda r, part=part: st.consumable_hours(r.consumables, part),
        )
        for part in st.CONSUMABLE_KEYS
    ),
    Robot360SensorDescription(
        key="last_status",
        translation_key="last_status",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.last_status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Robot360ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        Robot360Sensor(coordinator, sn, desc) for sn in coordinator.robots for desc in SENSORS
    )


class Robot360Sensor(Robot360Entity, SensorEntity):
    entity_description: Robot360SensorDescription
    # The raw status can be large; keep it out of the recorder.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: Robot360Coordinator, sn: str, desc: Robot360SensorDescription) -> None:
        super().__init__(coordinator, sn, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value_fn(self.robot)
        if isinstance(value, dict | list):
            return str(value)[:255]
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        # The mode sensor carries the complete raw status so new fields are visible in HA.
        if self.entity_description.key == "mode":
            return dict(self.status)
        return None
