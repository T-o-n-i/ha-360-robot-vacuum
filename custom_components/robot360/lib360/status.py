"""Interpretation of the status objects the robot pushes.

Field names confirmed on a 360 S6 (firmware 2.10.10), answer to infoType 20001:

    {"mode": "fullcharge", "subMode": "null", "elec": 100, "elecReal": 100,
     "workNoisy": "auto", "windPower": 0, "vol": 2, "led": 1, "mop": 0,
     "cleanArea": 39, "cleanTime": 2804, "errorState": [], "pos": [x, y], ...}

The other candidates are kept as fallbacks for other models.
"""

from __future__ import annotations

from typing import Any

MODE_KEYS = ("mode", "workMode", "runStatus", "cleanMode")
BATTERY_KEYS = ("elec", "elecReal", "batteryLevel", "battery_level")
FAN_KEYS = ("workNoisy", "noisy", "fanMode", "suction")
ERROR_KEYS = ("errorState", "errorCode", "errCode", "error", "faultCode")
VOLUME_KEYS = ("vol", "voice", "volume", "voiceLevel")

# Cloud mode -> generic activity name (matches HA VacuumActivity values).
MODE_MAP: dict[str, str] = {
    "charge": "docked",  # seen on S6
    "charging": "docked",
    "fullcharge": "docked",  # seen on S6
    "fullCharge": "docked",
    "smartClean": "cleaning",
    "sweep": "cleaning",  # seen on S6
    "reAppointClean": "cleaning",
    "aroundClean": "cleaning",
    "spotClean": "cleaning",
    "totalClean": "cleaning",
    "edgeClean": "cleaning",
    "pause": "paused",
    "idle": "idle",
    "standby": "idle",
    "sleep": "idle",
    "backcharge": "returning",  # seen on S6
    "backCharge": "returning",
    "chargeback": "returning",
    "chargeBack": "returning",
    "return": "returning",
    "gocharge": "returning",
    "goCharge": "returning",
    "error": "error",
}

FAN_SPEEDS = ["quiet", "auto", "strong"]


def _first(status: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, Any] | None:
    for key in keys:
        if key in status and status[key] not in (None, ""):
            return key, status[key]
    return None


def mode(status: dict[str, Any]) -> str | None:
    found = _first(status, MODE_KEYS)
    return str(found[1]) if found else None


def activity(status: dict[str, Any]) -> str | None:
    raw = mode(status)
    if raw is None:
        return None
    if found := MODE_MAP.get(raw) or MODE_MAP.get(raw.lower()):
        return found
    # Unknown mode names: guess from the word so HA does not show "unknown".
    low = raw.lower()
    if low.startswith("back") or "gocharge" in low or "return" in low:
        return "returning"
    if "clean" in low or "sweep" in low or "mop" in low:
        return "cleaning"
    if "charg" in low:
        return "docked"
    return None


def battery(status: dict[str, Any]) -> int | None:
    found = _first(status, BATTERY_KEYS)
    if not found:
        return None
    try:
        value = int(float(found[1]))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def fan_speed(status: dict[str, Any]) -> str | None:
    found = _first(status, FAN_KEYS)
    return str(found[1]) if found else None


def error_code(status: dict[str, Any]) -> str | None:
    """Active error codes as text, or None when there is no error."""
    found = _first(status, ERROR_KEYS)
    if not found:
        return None
    value = found[1]
    if isinstance(value, list | tuple):
        codes = [str(v.get("code", v) if isinstance(v, dict) else v) for v in value]
        # S10 sends [0] when there is no error.
        codes = [c for c in codes if c not in ("0", "", "null")]
        return ", ".join(codes) or None
    return None if str(value) in ("0", "", "null") else str(value)


def volume(status: dict[str, Any]) -> int | None:
    found = _first(status, VOLUME_KEYS)
    if not found:
        return None
    try:
        return int(found[1])
    except (TypeError, ValueError):
        return None


def clean_area(status: dict[str, Any]) -> int | None:
    value = status.get("cleanArea")
    return int(value) if isinstance(value, int | float) else None


def clean_minutes(status: dict[str, Any]) -> int | None:
    value = status.get("cleanTime")
    return round(value / 60) if isinstance(value, int | float) else None


# Answer to infoType 21015: operating seconds per part.
CONSUMABLE_KEYS = ("filter", "mainBrush", "sideBrush", "sensors")


def consumable_hours(consumables: dict[str, Any], key: str) -> float | None:
    value = consumables.get(key)
    return round(value / 3600, 1) if isinstance(value, int | float) else None
