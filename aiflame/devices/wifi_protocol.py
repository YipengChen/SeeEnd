"""Small HMAC handshake shared by the Hub and MicroPython firmware."""

import hashlib
import binascii

PORT = 8766
DISCOVERY_PORT = 8767


def proof(secret_hex, nonce, hub_id):
    key = binascii.unhexlify(secret_hex)
    if len(key) != 32 or len(nonce) != 24 or not isinstance(hub_id, str):
        raise ValueError("invalid handshake")
    message = (nonce + ":" + hub_id).encode()
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key += bytes(64 - len(key))
    outer = bytes(v ^ 0x5C for v in key)
    inner = bytes(v ^ 0x36 for v in key)
    return binascii.hexlify(
        hashlib.sha256(outer + hashlib.sha256(inner + message).digest()).digest()
    ).decode()


def equal(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a.encode(), b.encode()):
        diff |= x ^ y
    return diff == 0
