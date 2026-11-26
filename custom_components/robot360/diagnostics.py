"""Diagnostics download (raw device list and status messages)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_ANDROIDID, CONF_MID, CONF_TOKENS
from .coordinator import Robot360ConfigEntry

TO_REDACT = {CONF_TOKENS, "q", "t", CONF_USERNAME, CONF_PASSWORD, CONF_MID, CONF_ANDROIDID, "sn", "mac", "ip", "wifiMac", "ssid", "userId", "qid"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: Robot360ConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    push = coordinator.push
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "push": {
            "connected": push.connected if push else None,
            "failures": push.failures if push else None,
            "last_message": push.last_message if push else None,
        },
        "robots": [
            async_redact_data(
                {
                    "info": robot.info,
                    "status": robot.status,
                    "consumables": robot.consumables,
                    "last_status": robot.last_status.isoformat() if robot.last_status else None,
                    "recent_messages": list(robot.messages),
                },
                TO_REDACT,
            )
            for robot in coordinator.robots.values()
        ],
    }
