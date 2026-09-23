"""Small dependency-free password and access-token primitives for the MVP."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict

from app.shared.errors import ValidationError


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    """Hash a password using the standard-library scrypt KDF."""

    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_value, digest_value = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_value),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(digest, _unb64(digest_value))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, secret: str, ttl_seconds: int) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": user_id,
                "exp": int(time.time()) + ttl_seconds,
                "jti": secrets.token_urlsafe(16),
            },
            separators=(",", ":"),
        ).encode()
    )
    unsigned = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), unsigned, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64(signature)}"


def decode_access_token(token: str, secret: str) -> Dict[str, Any]:
    try:
        header, payload, signature = token.split(".")
        unsigned = f"{header}.{payload}".encode("ascii")
        expected = hmac.new(secret.encode("utf-8"), unsigned, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("invalid signature")
        data = json.loads(_unb64(payload))
        if not data.get("sub") or int(data.get("exp", 0)) <= int(time.time()):
            raise ValueError("expired token")
        return data
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError("Invalid or expired access token.", code="invalid_token") from exc
