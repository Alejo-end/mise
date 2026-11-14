"""Signatures on what the gateway tells an outside agent.

A hash shows that an answer did not change. It does not show who gave the answer: whoever
runs the server can make any hash. A signature does. The gateway signs each quote and each
order answer with an Ed25519 key. An outside agent checks the signature with the public key
from GET /gateway/key, keeps the signed answer, and can show it later.

In this prototype the gateway makes its own key on first start and keeps it in
data/gateway_key.pem. In production the key belongs to Valio, and Valio's systems sign.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical(body: dict) -> bytes:
    """The bytes that are signed: the JSON of the body with sorted keys and no extra spaces."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


class Signer:
    def __init__(self, path: Path | str):
        path = Path(path)
        if path.exists():
            self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            self.key = Ed25519PrivateKey.generate()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
            path.chmod(0o600)
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key = base64.b64encode(raw).decode()
        self.key_id = hashlib.sha256(raw).hexdigest()[:12]

    def sign(self, body: dict) -> dict:
        """The body with a "signature" entry added. The signature covers every other entry."""
        value = base64.b64encode(self.key.sign(canonical(body))).decode()
        return body | {"signature": {"alg": "Ed25519", "key_id": self.key_id, "value": value}}


def verify(signed: dict, public_key: str) -> bool:
    """True when the signature of a signed body is from the key and the body did not change."""
    body = {k: v for k, v in signed.items() if k != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            base64.b64decode(signed["signature"]["value"]), canonical(body))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False
