from __future__ import annotations

import hashlib
import secrets


CONTEXT = b"BOATRACE_PROSPECTIVE_SHADOW_V2"


def create_commitment(package_bytes: bytes, *, salt: bytes | None = None) -> dict[str, str]:
    salt = secrets.token_bytes(32) if salt is None else salt
    if len(salt) < 32:
        raise ValueError("salt_minimum_32_bytes")
    package_hash = hashlib.sha256(package_bytes).hexdigest()
    commitment = hashlib.sha256(CONTEXT + b"\x00" + salt + b"\x00" + bytes.fromhex(package_hash)).hexdigest()
    return {"packageSha256": package_hash, "saltHex": salt.hex(), "commitment": commitment}


def verify_reveal(package_bytes: bytes, salt_hex: str, expected_commitment: str) -> bool:
    try:
        actual = create_commitment(package_bytes, salt=bytes.fromhex(salt_hex))["commitment"]
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected_commitment)
