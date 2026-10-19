"""Password hashing and signed API tokens for staff endpoints."""
import hashlib
import hmac
import os
from functools import wraps

from flask import g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

TOKEN_MAX_AGE_SECONDS = 3600
_ITERATIONS = 120_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash a password with PBKDF2-SHA256; the random salt is stored next to the digest."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
    return hmac.compare_digest(candidate, digest_hex)


def create_token(secret_key: str, user_id: int) -> str:
    serializer = URLSafeTimedSerializer(secret_key, salt="shoplite-auth")
    return serializer.dumps({"user_id": user_id})


def verify_token(secret_key: str, token: str) -> int | None:
    """Return the user id inside a valid token, or None if it is tampered with or expired."""
    serializer = URLSafeTimedSerializer(secret_key, salt="shoplite-auth")
    try:
        data = serializer.loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return int(data["user_id"])


def require_auth(secret_key: str):
    """Decorator that rejects requests without a valid `Authorization: Bearer` token."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            token = header.removeprefix("Bearer ").strip()
            user_id = verify_token(secret_key, token) if token else None
            if user_id is None:
                return jsonify({"error": "authentication required"}), 401
            g.user_id = user_id
            return view(*args, **kwargs)
        return wrapper
    return decorator
