#!/usr/bin/env python3
"""Testlauf gegen die 360-Cloud, ohne Home Assistant.

Meldet sich mit deinem 360Robot-Konto an, listet die Roboter, fragt den Status ab
und schreibt alles (ohne Passwort und Sitzungsschlüssel) nach tools/probe_output.json.

    .venv/bin/python tools/probe.py
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "custom_components" / "robot360"))

import aiohttp  # noqa: E402

from lib360 import (  # noqa: E402
    DeviceIdentity,
    PushClient,
    PushMessage,
    Robot360Api,
    Robot360CaptchaRequired,
)
from lib360 import status as st  # noqa: E402

IDENTITY_FILE = HERE / ".identity.json"
OUTPUT_FILE = HERE / "probe_output.json"
WAIT_SECONDS = 40


def load_identity() -> DeviceIdentity:
    if IDENTITY_FILE.exists():
        return DeviceIdentity(**json.loads(IDENTITY_FILE.read_text()))
    identity = DeviceIdentity()
    IDENTITY_FILE.write_text(json.dumps(identity.as_dict()))
    return identity


async def login(api: Robot360Api) -> None:
    try:
        await api.async_login()
        return
    except Robot360CaptchaRequired:
        pass
    for _ in range(3):
        captcha = await api.async_get_captcha()
        img = HERE / "captcha.png"
        img.write_bytes(captcha.image)
        subprocess.run(["open", str(img)], check=False)
        code = input("360 will ein Captcha. Zeichen aus dem Bild (wurde geöffnet): ").strip()
        try:
            await api.async_login(captcha.sc, code)
            return
        except Robot360CaptchaRequired:
            print("Captcha falsch, neues kommt.")
    raise SystemExit("Captcha dreimal falsch.")


async def main() -> None:
    logging.basicConfig(level=logging.DEBUG if "-v" in sys.argv else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    username = input("E-Mail oder Handynummer, wie in der 360Robot-App (z. B. +4917...): ").strip()
    password = getpass.getpass("Passwort (wird nicht angezeigt): ")

    result: dict = {"devices": [], "messages": [], "raw_chunks_hex": [], "errors": []}
    async with aiohttp.ClientSession() as http:
        api = Robot360Api(http, username, password, load_identity())
        await login(api)
        session = api.session
        assert session is not None
        print(f"Login OK (qid …{session.qid[-3:]}), sid/pushKey erhalten.")

        devices = await api.async_get_devices()
        result["devices"] = devices
        print(f"{len(devices)} Roboter gefunden:")
        for dev in devices:
            print(f"  - {dev.get('title')}  sn={dev.get('sn')}  hardware={dev.get('hardware')}  online={dev.get('online')}")

        def on_raw(chunk: bytes) -> None:
            if len(result["raw_chunks_hex"]) < 10:
                result["raw_chunks_hex"].append(chunk[:400].hex())

        def on_msg(msg: PushMessage) -> None:
            result["messages"].append(
                {"t": round(time.time(), 1), "sn": msg.sn, "infoType": msg.info_type, "taskid": msg.task_id, "data": msg.data}
            )
            short = json.dumps(msg.data, ensure_ascii=False)[:300]
            print(f"\nPush von {msg.sn} infoType={msg.info_type}: {short}")
            if isinstance(msg.data, dict):
                print(
                    f"  -> erkannt: mode={st.mode(msg.data)} activity={st.activity(msg.data)} "
                    f"battery={st.battery(msg.data)} fan={st.fan_speed(msg.data)} error={st.error_code(msg.data)}"
                )

        push = PushClient(session.sid, session.push_key, on_msg, on_raw=on_raw)
        push.start()
        await asyncio.sleep(3)
        print(f"Push-Kanal verbunden: {push.connected}")

        for dev in devices:
            sn = str(dev.get("sn"))
            for info_type in ("20001", "21015"):
                try:
                    await api.async_send_command(sn, info_type, "")
                except Exception as err:  # noqa: BLE001
                    result["errors"].append(f"{sn} {info_type}: {err}")
                    print(f"Befehl {info_type} an {sn} fehlgeschlagen: {err}")

        print(f"Warte {WAIT_SECONDS} s auf Antworten …")
        await asyncio.sleep(WAIT_SECONDS)
        await push.stop()

    OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    print(f"\nFertig. {len(result['messages'])} Push-Nachrichten. Ergebnis: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
