"""Offline license keys for the paid plans (app/plans.py).

A key looks like `CL1.<payload>.<signature>`, both parts base64url without padding. The
payload is compact JSON - {"id", "plan", "licensee", "issued", "expires"}, with "expires"
null for a perpetual license - and the signature is Ed25519 over the payload bytes, made with
the vendor's private signing key. This module holds only the public halves
(VENDOR_PUBLIC_KEYS), so a key is checked with no network call: a private, self-hosted
assistant never reports anything about itself to anyone, not even to check its license.

Vendor workflow (`python -m app.cli ...`):
  license-keygen   once: creates the signing key (keep it off customer servers and out of
                   the repository) and prints the line to add to VENDOR_PUBLIC_KEYS
  license-issue    per sale: --plan pro --to "Customer name" --days 365
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from dataclasses import dataclass
from datetime import date, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = "CL1"
# Public keys (raw 32 bytes, base64url) whose signatures this build accepts. Add the line
# `license-keygen` prints; keep a retired key here while licenses signed with it are valid.
VENDOR_PUBLIC_KEYS: tuple[str, ...] = (
    "1of9kk-i6agKvqn8nUhuk5-J3PggVR5NwTlmQPfFE0w",  # 2026-10 signing key
)
GRACE_DAYS = 7  # a paid plan keeps working this long after its license expires


class LicenseError(ValueError):
    """The key is malformed or not signed by the vendor; the message is safe to show."""


@dataclass(frozen=True)
class License:
    id: str
    plan: str
    licensee: str
    issued: date
    expires: date | None  # None = perpetual

    def status(self, today: date | None = None) -> str:
        """'active', 'grace' (expired less than GRACE_DAYS ago) or 'expired'."""
        today = today or date.today()
        if self.expires is None or today <= self.expires:
            return "active"
        return "grace" if today <= self.expires + timedelta(days=GRACE_DAYS) else "expired"

    def to_dict(self, today: date | None = None) -> dict:
        return {
            "id": self.id,
            "plan": self.plan,
            "licensee": self.licensee,
            "issued": self.issued.isoformat(),
            "expires": self.expires.isoformat() if self.expires else None,
            "status": self.status(today),
        }


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse(key: str, *, public_keys: tuple[str, ...] | None = None) -> License:
    """The license a key holds, once its signature checks out against a vendor public key.
    Whether it has expired is License.status(); which plans exist is app/plans.py's business."""
    key = "".join(key.split())  # keys pasted from an email may wrap
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenseError("That isn't a Custodian license key (they start with CL1.).")
    try:
        payload, signature = _b64decode(parts[1]), _b64decode(parts[2])
    except (binascii.Error, ValueError):
        raise LicenseError("That license key is damaged; copy it again in full.") from None
    trusted = VENDOR_PUBLIC_KEYS if public_keys is None else public_keys
    if not trusted:
        raise LicenseError("This build of Custodian has no vendor key to check licenses with.")
    for public in trusted:
        try:
            Ed25519PublicKey.from_public_bytes(_b64decode(public)).verify(signature, payload)
            break
        except (InvalidSignature, ValueError):
            continue
    else:
        raise LicenseError("That license key isn't valid: its signature doesn't match.")
    try:
        data = json.loads(payload)
        return License(
            id=str(data["id"]),
            plan=str(data["plan"]),
            licensee=str(data["licensee"]),
            issued=date.fromisoformat(data["issued"]),
            expires=date.fromisoformat(data["expires"]) if data.get("expires") else None,
        )
    except (ValueError, KeyError, TypeError):
        raise LicenseError("That license key is signed but unreadable; ask for a new one.") from None


def issue(signing_key: Ed25519PrivateKey, plan: str, licensee: str, *, days: int | None, today: date | None = None) -> str:
    """A signed key for `licensee`. days=None makes it perpetual."""
    today = today or date.today()
    payload = json.dumps(
        {
            "id": f"lic_{secrets.token_hex(6)}",
            "plan": plan,
            "licensee": licensee,
            "issued": today.isoformat(),
            "expires": (today + timedelta(days=days)).isoformat() if days else None,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{PREFIX}.{_b64encode(payload)}.{_b64encode(signing_key.sign(payload))}"


def new_signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def signing_key_pem(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def load_signing_key(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError("That file isn't an Ed25519 license signing key.")
    return key


def public_key_text(key: Ed25519PrivateKey) -> str:
    """The base64url public key that goes in VENDOR_PUBLIC_KEYS."""
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64encode(raw)
