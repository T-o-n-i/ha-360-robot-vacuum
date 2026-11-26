"""Push channel of the 360 smart-home cloud.

The robots answer commands (and report state changes) on a persistent TCP socket,
not in the HTTP response.

Frame layout (big endian), as seen in cavefire/cn360-proxy and ioBroker.botslab360:

    magic     2  0x0005
    type      2  0x0002 handshake, 0x0003 data, 0x0004 ack, 0x0000 keep-alive
    hdr_len   2
    header    hdr_len bytes ASCII (data frames: "ack:<nr>...")
    rest_len  4  (only data frames)
    rest      rest_len bytes = seq(8) + product_id(4) + payload_len(4) + payload(JSON)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .crypto import aes_decrypt_b64

_LOGGER = logging.getLogger(__name__)

PUSH_HOST = "47.254.151.104"
PUSH_PORT = 443
PRODUCT_ID = 60009
KEEPALIVE_SECONDS = 25
READ_TIMEOUT_SECONDS = 240
MAGIC = b"\x00\x05"
TYPE_DATA = b"\x00\x03"
_DATA_RE = re.compile(rb'"data"\s*:\s*"([^"]*)"')


@dataclass
class PushMessage:
    sn: str
    info_type: str | None
    task_id: str | None
    data: Any
    raw: Any


def build_handshake(sid: str, ts_ms: int | None = None) -> bytes:
    ts_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)
    body = f"cv:1.7\nt:30\nu:{sid}@{PRODUCT_ID}\nts:{ts_ms}".encode("ascii")
    return MAGIC + b"\x00\x02" + struct.pack(">H", len(body)) + body


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str | bytes):
        text = value.strip()
        if text[:1] in ("{", "[", b"{", b"["):
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def decode_payload(payload: bytes, push_key: str) -> PushMessage | None:
    """Decode the JSON payload of a data frame into a PushMessage."""
    encrypted: str | None = None
    plain: Any = None
    try:
        outer = json.loads(payload)
    except ValueError:
        outer = None
    if isinstance(outer, dict):
        if isinstance(outer.get("data"), str) and outer.get("encrypt", 1):
            encrypted = outer["data"]
        else:
            plain = outer.get("data", outer)
    else:
        match = _DATA_RE.search(payload)
        if not match:
            return None
        encrypted = match.group(1).decode("ascii")

    if encrypted is not None:
        plain = aes_decrypt_b64(encrypted, push_key)

    frame = _maybe_json(plain)
    if not isinstance(frame, dict) or "sn" not in frame:
        _LOGGER.debug("Push frame without sn: %s", str(frame)[:300])
        return None

    body = _maybe_json(frame.get("data"))
    info_type = None
    task_id = None
    status: Any = body
    if isinstance(body, dict):
        info_type = body.get("infoType")
        task_id = body.get("taskid") or (body.get("extend") or {}).get("taskid")
        if body.get("data") is not None:
            status = _maybe_json(body["data"])
    info_type = info_type or frame.get("infoType")
    return PushMessage(
        sn=str(frame["sn"]),
        info_type=str(info_type) if info_type is not None else None,
        task_id=task_id,
        data=status,
        raw=frame,
    )


class FrameParser:
    """Splits the TCP stream into frames. Yields ``(ack_bytes, payload)`` for data frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[tuple[bytes, bytes]]:
        self._buf += chunk
        out: list[tuple[bytes, bytes]] = []
        while True:
            start = self._buf.find(MAGIC + TYPE_DATA)
            if start < 0:
                # Keep a possible partial magic at the end, drop the rest (handshake
                # replies, keep-alive echoes and other frames we don't need).
                del self._buf[: max(0, len(self._buf) - 3)]
                return out
            if start:
                del self._buf[:start]
            if len(self._buf) < 6:
                return out
            (hdr_len,) = struct.unpack(">H", self._buf[4:6])
            head_end = 6 + hdr_len
            if len(self._buf) < head_end + 4:
                return out
            (rest_len,) = struct.unpack(">I", self._buf[head_end : head_end + 4])
            total = head_end + 4 + rest_len
            if rest_len < 16 or rest_len > 8 * 1024 * 1024:
                # Not a real frame start, skip this magic.
                del self._buf[:2]
                continue
            if len(self._buf) < total:
                return out
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            ack = MAGIC + b"\x00\x04" + frame[4:head_end]
            rest = frame[head_end + 4 :]
            (payload_len,) = struct.unpack(">I", rest[12:16])
            out.append((ack, rest[16 : 16 + payload_len]))


class PushClient:
    """Keeps the push socket open, reconnects, and hands decoded messages to a callback."""

    def __init__(
        self,
        sid: str,
        push_key: str,
        on_message: Callable[[PushMessage], None],
        host: str = PUSH_HOST,
        port: int = PUSH_PORT,
        on_raw: Callable[[bytes], None] | None = None,
    ) -> None:
        self._sid = sid
        self._push_key = push_key
        self._on_message = on_message
        self._host = host
        self._port = port
        self._on_raw = on_raw  # debugging hook, gets every received TCP chunk
        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.connected = False
        self.last_message: float | None = None
        self.failures = 0

    def update_session(self, sid: str, push_key: str) -> None:
        """New credentials: reconnect with them."""
        if (sid, push_key) == (self._sid, self._push_key):
            return
        self._sid, self._push_key = sid, push_key
        if self._writer is not None:
            self._writer.close()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name="robot360_push")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        delay = 5
        while True:
            try:
                await self._connect_once()
                delay = 5
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                self.failures += 1
                _LOGGER.debug("Push channel error: %r", err)
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)

    async def _connect_once(self) -> None:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self._host, self._port), 20)
        self._writer = writer
        parser = FrameParser()
        keepalive: asyncio.Task | None = None
        try:
            writer.write(build_handshake(self._sid))
            await writer.drain()
            self.connected = True
            _LOGGER.debug("Push channel connected to %s:%s", self._host, self._port)
            keepalive = asyncio.get_running_loop().create_task(self._keepalive(writer))
            while True:
                # Status is polled every minute, so silence this long means a dead socket.
                chunk = await asyncio.wait_for(reader.read(65536), READ_TIMEOUT_SECONDS)
                if not chunk:
                    raise ConnectionError("push channel closed by server")
                if self._on_raw is not None:
                    self._on_raw(chunk)
                for ack, payload in parser.feed(chunk):
                    writer.write(ack)
                    self._handle_payload(payload)
                await writer.drain()
        finally:
            if keepalive:
                keepalive.cancel()
            self._writer = None
            writer.close()

    async def _keepalive(self, writer: asyncio.StreamWriter) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            writer.write(MAGIC + b"\x00\x00")
            await writer.drain()

    def _handle_payload(self, payload: bytes) -> None:
        try:
            msg = decode_payload(payload, self._push_key)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not decode push payload (%r): %s", err, payload[:200])
            return
        if msg is None:
            return
        self.failures = 0
        self.last_message = time.time()
        try:
            self._on_message(msg)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error in push message handler")
