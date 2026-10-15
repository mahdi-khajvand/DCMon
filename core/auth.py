from __future__ import annotations

import hashlib
import secrets
import time
from typing import Optional

import pandas as pd

from .config import USERS_FILE


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == str(password_hash)


def ensure_users_file() -> None:
    """اگر فایل کاربران وجود ندارد، فقط هدر خالی بساز (بدون یوزر پیش‌فرض)."""
    if USERS_FILE.exists():
        return
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        columns=["username", "password_hash", "role", "allowed_dcs", "is_active"]
    ).to_csv(USERS_FILE, index=False)


def load_users() -> pd.DataFrame:
    ensure_users_file()
    try:
        df = pd.read_csv(USERS_FILE)
        if df is None or df.empty:
            return pd.DataFrame(
                columns=["username", "password_hash", "role", "allowed_dcs", "is_active"]
            )
        return df
    except Exception:
        return pd.DataFrame(
            columns=["username", "password_hash", "role", "allowed_dcs", "is_active"]
        )


def has_active_admin() -> bool:
    users = load_users()
    if users.empty:
        return False
    if "role" not in users.columns:
        return False
    active = users[
        (users["role"].astype(str).str.lower() == "admin")
        & (users["is_active"].astype(str).isin(["1", "True", "true", "yes"]))
    ]
    return not active.empty


def needs_setup() -> bool:
    """اگر هیچ ادمین فعالی نباشد، سیستم در حالت راه‌اندازی اولیه است."""
    return not has_active_admin()


def bootstrap_setup_session() -> Optional[dict]:
    """ورود موقت بدون پسورد فقط وقتی هنوز ادمین ساخته نشده."""
    if not needs_setup():
        return None
    token = secrets.token_urlsafe(32)
    session = {
        "user": "setup",
        "role": "admin",
        "allowed_dcs": "all",
        "exp": time.time() + 86400,  # 24h
        "setup_mode": True,
    }
    _TOKENS[token] = session
    return {"token": token, "user": "setup", "role": "admin", "allowed_dcs": "all", "setup_mode": True}


def parse_allowed_dcs(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "all"
    value = str(value).strip()
    if value.lower() == "all" or not value:
        return "all"
    return {int(x.strip()) for x in value.split(",") if x.strip().isdigit()}


# token -> {user, role, allowed_dcs, exp}
_TOKENS: dict[str, dict] = {}


def login(username: str, password: str) -> Optional[dict]:
    users = load_users()
    if users.empty:
        return None
    match = users[
        (users["username"].astype(str) == str(username).strip())
        & (users["is_active"].astype(str).isin(["1", "True", "true"]))
    ]
    if match.empty:
        return None
    row = match.iloc[0]
    if not verify_password(password, row["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    session = {
        "user": str(row["username"]),
        "role": str(row["role"]),
        "allowed_dcs": parse_allowed_dcs(row.get("allowed_dcs")),
        "exp": time.time() + 86400 * 7,
    }
    _TOKENS[token] = session
    return {"token": token, **{k: v for k, v in session.items() if k != "exp"}}


def resolve_token(token: str) -> Optional[dict]:
    sess = _TOKENS.get(token)
    if not sess:
        return None
    if sess["exp"] < time.time():
        _TOKENS.pop(token, None)
        return None
    return sess


def is_admin(sess: dict) -> bool:
    return str(sess.get("role", "")).lower() == "admin"


def can_edit(sess: dict) -> bool:
    return str(sess.get("role", "")).lower() in {"admin", "editor"}
