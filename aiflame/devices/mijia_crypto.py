"""Wire encryption for MIoT cloud requests and miIO LAN packets."""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding


def rc4(data, key):
    # MIoT cloud uses RC4 with the first 1024 stream bytes discarded.
    state, j = list(range(256)), 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    i = j = 0
    output = bytearray()
    for position, value in enumerate(bytes(1024) + data):
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        transformed = value ^ state[(state[i] + state[j]) % 256]
        if position >= 1024:
            output.append(transformed)
    return bytes(output)


def aes(data, key, iv, decrypt=False):
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    if decrypt:
        operation = cipher.decryptor()
        clear = operation.update(data) + operation.finalize()
        unpad = padding.PKCS7(128).unpadder()
        return unpad.update(clear) + unpad.finalize()
    pad = padding.PKCS7(128).padder()
    padded = pad.update(data) + pad.finalize()
    operation = cipher.encryptor()
    return operation.update(padded) + operation.finalize()
