from __future__ import annotations

import hashlib
import secrets

from .db import execute, fetch_one
from .errors import ValidationError


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()


def create_user(conn, username: str, password: str) -> None:
    username = username.strip()
    if not username or len(username) < 3:
        raise ValidationError("Имя пользователя: минимум 3 символа")
    if len(password) < 5:
        raise ValidationError("Пароль: минимум 5 символов")
    salt = secrets.token_hex(16)
    password_hash = _hash_password(password, salt)
    existing = fetch_one(conn, "SELECT id FROM users WHERE username = %s", (username,))
    if existing:
        raise ValidationError(f"Пользователь «{username}» уже существует")
    execute(
        conn,
        "INSERT INTO users (username, password_hash, salt) VALUES (%s, %s, %s)",
        (username, password_hash, salt),
    )


def verify_user(conn, username: str, password: str) -> dict | None:
    row = fetch_one(
        conn,
        "SELECT id, username, password_hash, salt FROM users WHERE username = %s",
        (username.strip(),),
    )
    if not row:
        return None
    expected = _hash_password(password, row["salt"])
    if secrets.compare_digest(expected, row["password_hash"]):
        return {"id": row["id"], "username": row["username"]}
    return None
