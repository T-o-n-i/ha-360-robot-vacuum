"""Offline tests for crypto, frame parsing and payload decoding."""

from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "robot360"))

from lib360 import crypto  # noqa: E402
from lib360 import status as st  # noqa: E402
from lib360.push import FrameParser, build_handshake, decode_payload  # noqa: E402

NODE = shutil.which("node")
PUSH_KEY = "0123456789abcdef0123456789abcdef"


def node(js: str) -> str:
    return subprocess.run([NODE, "-e", js], capture_output=True, text=True, check=True).stdout.strip()


def test_des_known_vector() -> None:
    # Classic DES test vector: key 133457799BBCDFF1, plaintext 0123456789ABCDEF -> 85E813540F0AB405
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

    key = bytes.fromhex("133457799BBCDFF1")
    enc = Cipher(crypto.TripleDES(key * 3), modes.ECB()).encryptor()
    assert (enc.update(bytes.fromhex("0123456789ABCDEF")) + enc.finalize()).hex().upper() == "85E813540F0AB405"


def test_des_roundtrip() -> None:
    text = "username=a%40b.de&password=abc&x=äöü"
    assert crypto.des_decrypt_b64(crypto.des_encrypt_b64(text, "AbCd1234"), "AbCd1234") == text


def test_aes_roundtrip() -> None:
    assert crypto.aes_decrypt_b64(crypto.aes_encrypt_b64('{"a":1}', PUSH_KEY), PUSH_KEY) == '{"a":1}'


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_aes_matches_node() -> None:
    ours = crypto.aes_encrypt_b64('{"sn":"x","data":"y"}', PUSH_KEY)
    theirs = node(
        "const c=require('crypto');const k=Buffer.from('%s','utf8').subarray(0,16);"
        "const e=c.createCipheriv('aes-128-cbc',k,k);"
        "console.log(Buffer.concat([e.update('{\"sn\":\"x\",\"data\":\"y\"}'),e.final()]).toString('base64'))" % PUSH_KEY
    )
    assert ours == theirs


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_sig_and_uri_encoding_match_node() -> None:
    params = {"b": "2", "A": "x y", "a": "ä@+/", "sig": "ignored"}
    js_sig = node(
        "const c=require('crypto');const p=%s;let b='';for(const k of Object.keys(p).filter(k=>k!=='sig').sort())b+=k+'='+p[k];"
        "console.log(c.createHash('md5').update(Buffer.from(b+'i7v2m5x6q','utf8')).digest('hex'))" % json.dumps(params)
    )
    assert crypto.compute_sig(params, "i7v2m5x6q") == js_sig
    value = "a b@c.de/ä+!'()*~"
    assert crypto._encode_uri_component(value) == node(f"console.log(encodeURIComponent({json.dumps(value)}))")


def test_envelope_rsa_and_des() -> None:
    parad, key, des_key = crypto.build_envelope({"x": "1", "y": "a b"})
    assert len(base64.b64decode(key)) == 128  # 1024-bit RSA block
    assert crypto.des_decrypt_b64(parad, des_key) == "x=1&y=a%20b"


def test_handshake_matches_iobroker_bytes() -> None:
    sid = "a" * 32
    ts = 1700000000000
    expected = b"\x00\x05\x00\x02\x00Ecv:1.7\n" + b"t:30\n" + f"u:{sid}@60009\n".encode() + f"ts:{ts}".encode()
    assert build_handshake(sid, ts) == expected


def _data_frame(payload: bytes, ack: bytes = b"ack:42", seq: int = 7, product: int = 60009) -> bytes:
    rest = struct.pack(">QII", seq, product, len(payload)) + payload
    return b"\x00\x05\x00\x03" + struct.pack(">H", len(ack)) + ack + struct.pack(">I", len(rest)) + rest


def _encrypted_payload(sn: str, inner: dict) -> bytes:
    frame = {"sn": sn, "data": json.dumps(inner)}
    return json.dumps({"data": crypto.aes_encrypt_b64(json.dumps(frame), PUSH_KEY), "devType": 3, "encrypt": 1}).encode()


def test_parser_splits_and_acks_across_chunks() -> None:
    p1 = _encrypted_payload("SN1", {"infoType": 20001, "taskid": "t1", "data": {"mode": "charge", "elec": 88}})
    p2 = _encrypted_payload("SN2", {"data": {"mode": "smartClean"}})
    stream = b"\x00\x05\x00\x00" + _data_frame(p1) + _data_frame(p2, ack=b"ack:43")
    parser = FrameParser()
    out = []
    for i in range(0, len(stream), 7):  # feed in small pieces
        out += parser.feed(stream[i : i + 7])
    assert [ack for ack, _ in out] == [b"\x00\x05\x00\x04\x00\x06ack:42", b"\x00\x05\x00\x04\x00\x06ack:43"]
    msg = decode_payload(out[0][1], PUSH_KEY)
    assert msg is not None
    assert (msg.sn, msg.info_type, msg.task_id) == ("SN1", "20001", "t1")
    assert msg.data == {"mode": "charge", "elec": 88}
    assert st.activity(msg.data) == "docked" and st.battery(msg.data) == 88
    msg2 = decode_payload(out[1][1], PUSH_KEY)
    assert msg2 is not None and st.activity(msg2.data) == "cleaning"


def test_iobroker_style_ack_equals_ours() -> None:
    frame = _data_frame(_encrypted_payload("SN1", {"data": {}}))
    nul = frame.index(0, 5)
    theirs = bytearray(frame[:nul])
    theirs[3] = 4
    (ours, _), = FrameParser().feed(frame)
    assert bytes(theirs) == ours


def test_decode_payload_regex_fallback() -> None:
    inner = crypto.aes_encrypt_b64(json.dumps({"sn": "SN9", "data": {"data": {"mode": "pause"}}}), PUSH_KEY)
    junk = b'garbage {"data":"' + inner.encode() + b'", broken'
    msg = decode_payload(junk, PUSH_KEY)
    assert msg is not None and msg.sn == "SN9" and st.activity(msg.data) == "paused"


def test_wrong_captcha_is_detected() -> None:
    from lib360.api import _is_captcha_error

    assert _is_captcha_error("Verification code error, please re-enter")
    assert _is_captcha_error("验证码错误")
    assert not _is_captcha_error("Wrong password")


def test_s6_modes_seen_live() -> None:
    assert [st.activity({"mode": m}) for m in ("fullcharge", "sweep", "backcharge", "charge")] == [
        "docked", "cleaning", "returning", "docked",
    ]
    assert st.activity({"mode": "edgeSweep"}) == "cleaning"
    assert st.activity({"mode": "somethingNew"}) is None


def test_error_state_zero_means_no_error() -> None:
    # 360 S10 reports errorState [0] while docked without a fault.
    assert st.error_code({"mode": "charge", "errorState": [0]}) is None
    assert st.error_code({"errorState": []}) is None
    assert st.error_code({"errorState": [0, 105]}) == "105"
    assert st.error_code({"errorState": [{"code": 3}]}) == "3"
