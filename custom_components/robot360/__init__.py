"""360 Robot Vacuum (360Robot app / q.smart.360.cn) integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ANDROIDID, CONF_MID, CONF_TOKENS
from .coordinator import Robot360ConfigEntry, Robot360Coordinator
from .lib360 import AccountTokens, DeviceIdentity, Robot360Api

PLATFORMS = [Platform.NUMBER, Platform.SENSOR, Platform.VACUUM]


async def async_setup_entry(hass: HomeAssistant, entry: Robot360ConfigEntry) -> bool:
    api = Robot360Api(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        DeviceIdentity(mid=entry.data[CONF_MID], androidid=entry.data[CONF_ANDROIDID]),
        tokens=AccountTokens(**entry.data[CONF_TOKENS]) if entry.data.get(CONF_TOKENS) else None,
    )
    coordinator = Robot360Coordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_shutdown)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Robot360ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
