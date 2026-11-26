"""Client for the 360Robot cloud (passport.360.cn + q.smart.360.cn).

Login flow and constants are ported from TA2k/ioBroker.botslab360 (MIT),
command payloads from juetthei/ha-360-vacuum (MIT).

This module does not import Home Assistant so it can be used from a plain script.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import aiohttp

from .crypto import build_envelope, compute_sig, des_decrypt_b64, md5_hex, random_hex

_LOGGER = logging.getLogger(__name__)

# --- 360 account (QUC) login ---------------------------------------------------
LOGIN_URL = "https://passport.360.cn/request.php"
QUC_FROM = "mpl_smarthome_and"
QUC_APP = "360Robot"
QUC_SIG_SECRET = "i7v2m5x6q"
QUC_UA = f"360accounts andv3.2.4.6 {QUC_FROM}"
ERRNO_CAPTCHA = 5010
ERRNO_ACCOUNT_NOT_FOUND = 5007

# --- Smart-home API ------------------------------------------------------------
BASE_URL = "https://q.smart.360.cn"
APP_UA = "QihooSuperApp_NoPods/11.1.0 (iPhone; iOS 14.8; Scale/3.00)"
MINT_UA = "qhsa-iphone-11.1.0"
APP_FROM = "mpc_ios"
DEV_TYPE = "3"
SESSION_EXPIRED_ERRNOS = {102, 103}

TIMEOUT = aiohttp.ClientTimeout(total=20)


class Robot360Error(Exception):
    """Generic API error."""

    def __init__(self, message: str, errno: int | None = None) -> None:
        super().__init__(message)
        self.errno = errno


class Robot360AuthError(Robot360Error):
    """Wrong credentials or account not usable."""


class Robot360CaptchaRequired(Robot360Error):
    """The login server wants a picture captcha to be solved."""


class Robot360SessionExpired(Robot360Error):
    """sid no longer accepted, a new login is needed."""


@dataclass
class DeviceIdentity:
    """Stable fake phone identity. Changing it on every login triggers captchas."""

    mid: str = field(default_factory=lambda: random_hex(16))
    androidid: str = field(default_factory=lambda: random_hex(8))

    def as_dict(self) -> dict[str, str]:
        return {"mid": self.mid, "androidid": self.androidid}


@dataclass
class AccountTokens:
    """Result of the password login (Q/T cookies of the 360 account)."""

    q: str
    t: str
    qid: str

    def as_dict(self) -> dict[str, str]:
        return {"q": self.q, "t": self.t, "qid": self.qid}


@dataclass
class Session:
    q: str
    t: str
    qid: str
    sid: str
    push_key: str
    created: float = field(default_factory=time.time)

    @property
    def cookie(self) -> str:
        return f"q={self.q};t={self.t};qid={self.qid};sid={self.sid}"


@dataclass
class Captcha:
    image: bytes
    sc: str


def _is_captcha_error(errmsg: str) -> bool:
    text = errmsg.lower()
    return any(word in text for word in ("verification code", "captcha", "验证码"))


def _base_device_params(identity: DeviceIdentity) -> dict[str, str]:
    return {
        "os_sdk_version": "android_33",
        "mid": identity.mid,
        "quc_sdk_version": "v3.2.4.6",
        "mname": "",
        "ua": "Dalvik/2.1.0 (Linux; U; Android 13; sdk_gphone64_arm64 Build/TE1A.240213.009)",
        "os_manufacturer": "Google",
        "mSystemVersion": "android 13",
        "os_board": "goldfish_arm64",
        "os_model": "sdk_gphone64_arm64",
        "quc_lang": "en",
        "sh": "2337.0",
        "from": QUC_FROM,
        "oaid": "",
        "app": QUC_APP,
        "ui_ver": "4.3.4.1-alert-ui",
        "res_mode": "1",
        "sw": "1080.0",
        "format": "json",
        "qh_id": "",
        "device_os": "android",
        "device_lang": "zh-CN",
        "v": "2.24.0",
        "androidid": identity.androidid,
        "sdpi": "2.625",
    }


class Robot360Api:
    """Async client. One instance per 360 account."""

    def __init__(
        self,
        http: aiohttp.ClientSession,
        username: str,
        password: str,
        identity: DeviceIdentity,
        lang: str = "de_DE",
        country: str = "DE",
        tokens: AccountTokens | None = None,
    ) -> None:
        self._http = http
        self._username = username
        self._password_md5 = md5_hex(password)
        self._identity = identity
        self._lang = lang
        self._country = country
        self._login_lock = asyncio.Lock()
        self.session: Session | None = None
        # Long-lived account login (like the app keeps it). Reused so the password,
        # and with it the captcha, is only needed once.
        self.tokens: AccountTokens | None = tokens

    # ------------------------------------------------------------------ login

    async def _quc_request(self, method: str, extra: dict[str, str]) -> tuple[aiohttp.ClientResponse, bytes, str]:
        now_ms = int(time.time() * 1000)
        params = {**_base_device_params(self._identity), **extra}
        params.setdefault("vt_guid", str(now_ms))
        params.setdefault("trace_id", f"src_and_1916_{now_ms}")
        params["method"] = method
        # Keep key order like the app (method/trace_id sit where the extras put them).
        params["sig"] = compute_sig(params, QUC_SIG_SECRET)
        parad, key, des_key = build_envelope(params)
        body = {
            "device_lang": "zh-CN",
            "trace_id": params["trace_id"],
            "quc_lang": "en",
            "method": method,
            "from": QUC_FROM,
            "parad": parad,
            "key": key,
        }
        async with self._http.post(
            LOGIN_URL,
            data=body,
            headers={"User-Agent": QUC_UA, "Connection": "close"},
            timeout=TIMEOUT,
        ) as resp:
            raw = await resp.read()
            return resp, raw, des_key

    async def async_get_captcha(self) -> Captcha:
        resp, raw, _ = await self._quc_request("UserIntf.getCaptcha", {})
        return Captcha(image=raw, sc=unquote(resp.headers.get("sc", "")))

    async def _quc_login(self, captcha_sc: str | None, captcha_code: str | None) -> tuple[str, str, str]:
        now_ms = int(time.time() * 1000)
        extra = {
            "loginType": "801",
            "vt_guid": str(now_ms),
            "is_keep_alive": "1",
            "needDeviceCheck": "0",
            "trace_id": f"src_and_1916_{now_ms}",
            "method": "UserIntf.login",
            "head_type": "q",
            "sec_type": "bool",
            "fields": "qid,username,nickname,loginemail,head_pic,mobile",
            "username": self._username,
            "password": self._password_md5,
        }
        if captcha_sc and captcha_code:
            extra |= {"sc": captcha_sc, "uc": captcha_code, "captchaType": "graph"}
        _, raw, des_key = await self._quc_request("UserIntf.login", extra)
        try:
            ret = json.loads(raw)["ret"]
            decoded = json.loads(des_decrypt_b64(ret, des_key))
        except (ValueError, KeyError, TypeError) as err:
            raise Robot360Error(f"Unexpected login response: {raw[:200]!r}") from err

        errno = int(decoded.get("errno", -1))
        errmsg = str(decoded.get("errmsg", ""))
        _LOGGER.debug("360 account login errno=%s errmsg=%s", errno, errmsg)
        if errno == ERRNO_CAPTCHA or (errno != 0 and _is_captcha_error(errmsg)):
            # A wrongly typed captcha comes back with its own errno and text.
            raise Robot360CaptchaRequired(f"{errmsg or 'captcha required'} (errno {errno})", errno)
        if errno != 0 or not decoded.get("user"):
            raise Robot360AuthError(f"{errmsg or 'login failed'} (errno {errno})", errno)
        user = decoded["user"]
        return unquote(user["q"]), unquote(user["t"]), str(user["qid"])

    async def _mint_sid(self, q: str, t: str, qid: str) -> tuple[str, str]:
        client_info = {
            "release": "appstore",
            "brand": "iPhone",
            "model": "iPhone10,5",
            "notifyId": "aa0ad645269de676a5ee6a728ba13b777ed3d4aa4d0e08a578097fbe78768b02",
            "lang": self._lang,
            "imei": "f3bc82b802bd91a51d0dcc6499efeba3",
        }
        data = await self._post(
            "/common/user/login",
            {
                "clientInfo": json.dumps(client_info, separators=(",", ":")),
                "lang": self._lang,
                "phoneNum": "",
                "taskid": str(uuid.uuid4()),
            },
            cookie=f"q={q};t={t};qid={qid}",
            user_agent=MINT_UA,
        )
        payload = data.get("data") or {}
        if not payload.get("sid") or not payload.get("pushKey"):
            raise Robot360Error("Login worked but no sid/pushKey was returned")
        return str(payload["sid"]), str(payload["pushKey"])

    async def _start_session(self, tokens: AccountTokens) -> Session:
        sid, push_key = await self._mint_sid(tokens.q, tokens.t, tokens.qid)
        self.session = Session(q=tokens.q, t=tokens.t, qid=tokens.qid, sid=sid, push_key=push_key)
        return self.session

    async def async_login(self, captcha_sc: str | None = None, captcha_code: str | None = None) -> Session:
        """Password login: 360 account -> q/t/qid -> smart-home sid + pushKey."""
        async with self._login_lock:
            q, t, qid = await self._quc_login(captcha_sc, captcha_code)
            self.tokens = AccountTokens(q=q, t=t, qid=qid)
            _LOGGER.debug("360 password login ok for qid %s", qid)
            return await self._start_session(self.tokens)

    async def async_connect(self) -> Session:
        """New sid from the saved account tokens; password login only if they no longer work."""
        if self.tokens is not None:
            async with self._login_lock:
                try:
                    return await self._start_session(self.tokens)
                except Robot360Error as err:  # tokens expired or revoked
                    _LOGGER.debug("Saved 360 tokens rejected (%s), using password", err)
                    self.tokens = None
        return await self.async_login()

    # ------------------------------------------------------------------ requests

    async def _post(
        self,
        path: str,
        form: dict[str, str],
        *,
        cookie: str | None = None,
        user_agent: str = APP_UA,
    ) -> dict[str, Any]:
        if cookie is None:
            if self.session is None:
                raise Robot360SessionExpired("not logged in")
            cookie = self.session.cookie
        headers = {
            "Accept": "*/*",
            "Cookie": cookie,
            "User-Agent": user_agent,
            "Accept-Language": "de-DE;q=1, en-DE;q=0.8",
        }
        async with self._http.post(BASE_URL + path, data=form, headers=headers, timeout=TIMEOUT) as resp:
            if resp.status == 401:
                raise Robot360SessionExpired("HTTP 401", 102)
            text = await resp.text()
        try:
            data = json.loads(text)
        except ValueError as err:
            raise Robot360Error(f"{path}: invalid JSON (HTTP {resp.status}): {text[:200]}") from err
        errno = int(data.get("errno", -1))
        if errno in SESSION_EXPIRED_ERRNOS:
            raise Robot360SessionExpired(str(data.get("errmsg", "session expired")), errno)
        if errno != 0:
            raise Robot360Error(f"{path}: errno {errno}: {data.get('errmsg', '')}", errno)
        return data

    async def _post_with_relogin(self, path: str, form: dict[str, str]) -> dict[str, Any]:
        try:
            return await self._post(path, form)
        except Robot360SessionExpired:
            _LOGGER.debug("Session expired, logging in again")
            await self.async_connect()
            return await self._post(path, form)

    def _common_form(self) -> dict[str, str]:
        return {
            "countryId": self._country,
            "devType": DEV_TYPE,
            "from": APP_FROM,
            "lang": self._lang,
            "taskid": str(uuid.uuid4()),
        }

    async def async_get_devices(self) -> list[dict[str, Any]]:
        data = await self._post_with_relogin("/common/dev/GetList", self._common_form())
        payload = data.get("data")
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("list", "devList", "devices", "cleanDevList"):
                if isinstance(payload.get(key), list):
                    return payload[key]
        return []

    async def async_send_command(self, sn: str, info_type: str | int, data: dict[str, Any] | str = "") -> str:
        """Send a command. Returns the taskid; the robot answers on the push channel."""
        form = self._common_form()
        form |= {
            "sn": sn,
            "infoType": str(info_type),
            "data": data if isinstance(data, str) else json.dumps(data, separators=(",", ":")),
        }
        _LOGGER.debug("cmd/send sn=%s infoType=%s data=%s", sn, form["infoType"], form["data"])
        await self._post_with_relogin("/clean/cmd/send", form)
        return form["taskid"]

    # ------------------------------------------------------------------ commands

    async def async_request_status(self, sn: str) -> str:
        return await self.async_send_command(sn, 20001, "")

    async def async_start(self, sn: str) -> None:
        await self.async_send_command(sn, 21005, {"mode": "smartClean", "globalCleanTimes": 1})

    async def async_pause(self, sn: str) -> None:
        await self.async_send_command(sn, 21017, {"cmd": "pause"})

    async def async_resume(self, sn: str) -> None:
        await self.async_send_command(sn, 21017, {"cmd": "continue"})

    async def async_return_to_base(self, sn: str) -> None:
        await self.async_send_command(sn, 21012, {"cmd": "start"})

    async def async_set_fan_speed(self, sn: str, speed: str) -> None:
        await self.async_send_command(sn, 21022, {"cmd": speed})

    async def async_locate(self, sn: str) -> None:
        await self.async_send_command(sn, 21020, {"ctrlCode": 3010})

    async def async_set_volume(self, sn: str, volume: int) -> None:
        await self.async_send_command(sn, 21024, {"cmd": "setVolume", "value": int(volume)})

    async def async_request_consumables(self, sn: str) -> None:
        await self.async_send_command(sn, 21015, "")
