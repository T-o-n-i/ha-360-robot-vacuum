"""Home Assistant level tests with a faked 360 cloud."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.robot360.const import DOMAIN
from custom_components.robot360.lib360 import (
    Captcha,
    PushMessage,
    Robot360Api,
    Robot360AuthError,
    Robot360CaptchaRequired,
    Robot360SessionExpired,
)

DEVICES = [
    {"sn": "SN0000001", "title": "Erdgeschoss", "hardware": "S6", "online": 1},
    {"sn": "SN0000002", "title": "Obergeschoss", "hardware": "S6", "online": 1},
]
# Real answers of a 360 S6 (firmware 2.10.10), battery changed to 77.
S6_STATUS = {
    "isInForbidMode": 0, "pos": [-2576, 127], "phi": -2302, "reliable": 0, "elec": 77, "elecReal": 77,
    "cleanArea": 39, "allArea": 0, "cleanTime": 2804, "allTime": 0, "mode": "fullcharge", "subMode": "null",
    "windPower": 0, "workNoisy": "auto", "vol": 2, "led": 1, "mop": 0, "AreaSettingId": 1699533396,
    "errorState": [], "timeStamp": 230287524807,
}
S6_CONSUMABLES = {
    "battery": 172589580, "filter": 76310, "mainBrush": 76310, "sideBrush": 76310, "sensors": 401036,
    "reserve1": 567304, "reserve2": 567304, "reserve3": 567304,
}
ENTRY_DATA = {"username": "+4917000000", "password": "pw", "mid": "m" * 32, "androidid": "a" * 16}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeCloud:
    """Replaces only the network calls of the real Robot360Api."""

    def __init__(self) -> None:
        self.captcha_needed = False  # 360 wants a captcha for every password login
        self.tokens_valid = True
        self.password_logins = 0
        self.quc_login = AsyncMock(side_effect=self._quc_login)
        self.mint_sid = AsyncMock(side_effect=self._mint_sid)
        self.get_devices = AsyncMock(return_value=DEVICES)
        self.send = AsyncMock(return_value="taskid")
        self.get_captcha = AsyncMock(return_value=Captcha(image=b"\x89PNG", sc="tok"))

    async def _quc_login(self, sc, code):
        self.password_logins += 1
        if self.captcha_needed and not code:
            raise Robot360CaptchaRequired("captcha required", 5010)
        if self.captcha_needed and code != "8544":
            raise Robot360CaptchaRequired("Verification code error", 1)
        return "q", "t", "12345"

    async def _mint_sid(self, q, t, qid):
        if not self.tokens_valid:
            raise Robot360SessionExpired("expired", 102)
        return "s" * 32, "p" * 32


@pytest.fixture
def cloud():
    fake = FakeCloud()
    with (
        patch.object(Robot360Api, "_quc_login", fake.quc_login),
        patch.object(Robot360Api, "_mint_sid", fake.mint_sid),
        patch.object(Robot360Api, "async_get_devices", fake.get_devices),
        patch.object(Robot360Api, "async_send_command", fake.send),
        patch.object(Robot360Api, "async_get_captcha", fake.get_captcha),
    ):
        yield fake


@pytest.fixture
def push():
    with patch("custom_components.robot360.coordinator.PushClient", autospec=True) as cls:
        yield cls


async def test_config_flow(hass: HomeAssistant, cloud, push) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "+4917000000", "password": "pw"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "12345"
    assert set(result["data"]) == {"username", "password", "mid", "androidid", "tokens"}
    assert result["data"]["tokens"] == {"q": "q", "t": "t", "qid": "12345"}


async def test_config_flow_bad_password(hass: HomeAssistant, cloud, push) -> None:
    cloud.quc_login.side_effect = Robot360AuthError("wrong", 5009)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "+4917000000", "password": "x"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_setup_push_and_commands(hass: HomeAssistant, cloud, push) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="12345")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("vacuum.erdgeschoss")
    assert state is not None
    assert hass.states.get("vacuum.obergeschoss") is not None
    assert hass.states.get("sensor.erdgeschoss_battery") is not None

    # Status arrives on the push channel.
    on_message = push.call_args.args[2]
    on_message(PushMessage(sn="SN0000001", info_type="20001", task_id=None, data=dict(S6_STATUS), raw={}))
    # Consumables answer has its own "battery" field; it must not end up in the status.
    on_message(PushMessage(sn="SN0000001", info_type="21015", task_id=None, data=dict(S6_CONSUMABLES), raw={}))
    await hass.async_block_till_done()
    state = hass.states.get("vacuum.erdgeschoss")
    assert state.state == "docked"
    assert state.attributes["fan_speed"] == "auto"
    assert hass.states.get("sensor.erdgeschoss_battery").state == "77"
    assert hass.states.get("sensor.erdgeschoss_status").state == "fullcharge"
    assert hass.states.get("sensor.erdgeschoss_error_code").state == "unknown"
    assert hass.states.get("sensor.erdgeschoss_cleaned_area").state == "39"
    assert hass.states.get("sensor.erdgeschoss_cleaning_time").state == "47"
    assert hass.states.get("sensor.erdgeschoss_filter_operating_time").state == "21.2"
    assert hass.states.get("number.erdgeschoss_volume").state == "2"

    await hass.services.async_call("number", "set_value", {"entity_id": "number.erdgeschoss_volume", "value": 5}, blocking=True)
    cloud.send.assert_awaited_with("SN0000001", 21024, {"cmd": "setVolume", "value": 5})
    assert hass.states.get("number.erdgeschoss_volume").state == "5"

    on_message(PushMessage(sn="SN0000001", info_type="20001", task_id=None, data={"errorState": [{"code": 12}]}, raw={}))
    await hass.async_block_till_done()
    assert hass.states.get("vacuum.erdgeschoss").state == "error"
    assert hass.states.get("sensor.erdgeschoss_error_code").state == "12"
    on_message(PushMessage(sn="SN0000001", info_type="20001", task_id=None, data={"errorState": []}, raw={}))

    await hass.services.async_call("vacuum", "start", {"entity_id": "vacuum.erdgeschoss"}, blocking=True)
    cloud.send.assert_awaited_with("SN0000001", 21005, {"mode": "smartClean", "globalCleanTimes": 1})
    await hass.services.async_call(
        "vacuum", "set_fan_speed", {"entity_id": "vacuum.erdgeschoss", "fan_speed": "strong"}, blocking=True
    )
    cloud.send.assert_awaited_with("SN0000001", 21022, {"cmd": "strong"})
    await hass.services.async_call("vacuum", "return_to_base", {"entity_id": "vacuum.obergeschoss"}, blocking=True)
    cloud.send.assert_awaited_with("SN0000002", 21012, {"cmd": "start"})
    await hass.services.async_call(
        "vacuum", "send_command",
        {"entity_id": "vacuum.erdgeschoss", "command": "21024", "params": {"cmd": "setVolume", "value": 3}},
        blocking=True,
    )
    cloud.send.assert_awaited_with("SN0000001", "21024", {"cmd": "setVolume", "value": 3})

    # Paused robot: start means resume.
    on_message(PushMessage(sn="SN0000001", info_type="20001", task_id=None, data={"mode": "pause"}, raw={}))
    await hass.services.async_call("vacuum", "start", {"entity_id": "vacuum.erdgeschoss"}, blocking=True)
    cloud.send.assert_awaited_with("SN0000001", 21017, {"cmd": "continue"})

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_bad_login_starts_reauth(hass: HomeAssistant, cloud, push) -> None:
    cloud.quc_login.side_effect = Robot360AuthError("wrong", 5009)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="12345")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_captcha_once_then_no_password_login(hass: HomeAssistant, cloud, push) -> None:
    """The bug seen live: after the captcha, setup logged in with the password again and looped."""
    cloud.captcha_needed = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "+4917000000", "password": "pw"})
    assert result["step_id"] == "captcha"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"captcha": "1111"})
    assert result["step_id"] == "captcha" and result["errors"] == {"base": "captcha_wrong"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"captcha": "8544"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    cloud.quc_login.assert_awaited_with("tok", "8544")
    await hass.async_block_till_done()

    entry = result["result"]
    logins_after_flow = cloud.password_logins
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert not [f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth"]
    assert hass.states.get("vacuum.erdgeschoss") is not None

    # A restart (reload) also gets by with the saved tokens.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert cloud.password_logins == logins_after_flow


async def test_expired_tokens_fall_back_to_password(hass: HomeAssistant, cloud, push) -> None:
    cloud.tokens_valid = False
    calls = {"n": 0}
    real = cloud._mint_sid

    async def mint(q, t, qid):
        calls["n"] += 1
        if calls["n"] == 1:  # old tokens rejected once
            raise Robot360SessionExpired("expired", 102)
        cloud.tokens_valid = True
        return await real(q, t, qid)

    cloud.mint_sid.side_effect = mint
    data = {**ENTRY_DATA, "tokens": {"q": "old", "t": "old", "qid": "12345"}}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="12345")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert cloud.password_logins == 1
    assert entry.data["tokens"] == {"q": "q", "t": "t", "qid": "12345"}
