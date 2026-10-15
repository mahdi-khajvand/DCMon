"""
Bale bot integration — settings, runner, and helpers.
Used by both the standalone bale_bot.py and the FastAPI app.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import BASE_DIR, DATA_DIR

log = logging.getLogger("bale_integration")

SETTINGS_FILE = DATA_DIR / "bale_settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "bot_token": "",
    "default_chat_id": "",
    "auto_alerts": True,
    "notify_service_stop": True,
    "polling": True,
    "mini_app_url": "",  # public HTTPS URL of the web panel (for Mini App)
    "last_offset": None,
}

_lock = threading.Lock()
_runner_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_status: dict[str, Any] = {
    "running": False,
    "bot_username": None,
    "bot_id": None,
    "last_error": None,
    "started_at": None,
}


def _read_settings_raw() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_settings() -> dict[str, Any]:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    out = dict(DEFAULT_SETTINGS)
    raw = _read_settings_raw()
    if raw:
        out.update(raw)
    else:
        env_token = os.environ.get("BALE_BOT_TOKEN", "").strip()
        if env_token:
            out["bot_token"] = env_token
            out["enabled"] = True
        try:
            SETTINGS_FILE.write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
    return out


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    current = dict(DEFAULT_SETTINGS)
    current.update(_read_settings_raw())
    current.update({k: v for k, v in settings.items() if v is not None})
    SETTINGS_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return current


def mask_token(token: str) -> str:
    if not token or len(token) < 10:
        return ""
    return token[:4] + "…" + token[-6:]


def public_settings() -> dict[str, Any]:
    s = load_settings()
    return {
        "enabled": bool(s.get("enabled")),
        "bot_token_masked": mask_token(str(s.get("bot_token") or "")),
        "has_token": bool(s.get("bot_token")),
        "default_chat_id": str(s.get("default_chat_id") or ""),
        "auto_alerts": bool(s.get("auto_alerts", True)),
        "notify_service_stop": bool(s.get("notify_service_stop", True)),
        "polling": bool(s.get("polling", True)),
        "mini_app_url": str(s.get("mini_app_url") or ""),
        "status": get_status(),
    }


def get_status() -> dict[str, Any]:
    with _lock:
        return dict(_status)


def _set_status(**kwargs):
    with _lock:
        _status.update(kwargs)


def bale_api(token: str, method: str, **params) -> dict:
    url = f"https://tapi.bale.ai/bot{token}/{method}"
    try:
        with httpx.Client(timeout=45.0) as client:
            r = client.post(url, json=params)
            return r.json()
    except Exception as e:
        return {"ok": False, "description": str(e)}


def check_token(token: str) -> dict:
    if not token:
        return {"ok": False, "description": "توکن خالی است"}
    res = bale_api(token, "getMe")
    if res.get("ok"):
        u = res["result"]
        return {
            "ok": True,
            "id": u.get("id"),
            "username": u.get("username"),
            "first_name": u.get("first_name"),
        }
    return {"ok": False, "description": res.get("description", "توکن نامعتبر")}


def _plain(text: str) -> str:
    import re as _re
    s = str(text or "")
    s = _re.sub(r"<[^>]*>", "", s)
    return s


def send_message(token: str, chat_id: str | int, text: str, **extra) -> dict:
    extra.pop("parse_mode", None)
    return bale_api(token, "sendMessage", chat_id=chat_id, text=_plain(text), **extra)


def set_mini_app_menu(token: str, web_app_url: str = "", text: str = "") -> dict:
    """Mini App intentionally disabled."""
    return {"ok": True, "disabled": True}




def notify_chat(text: str) -> bool:
    """ارسال اعلان به default_chat_id در صورت فعال بودن"""
    s = load_settings()
    token = s.get("bot_token") or ""
    chat_id = s.get("default_chat_id") or ""
    if not token or not chat_id:
        return False
    res = send_message(token, chat_id, text)
    return bool(res.get("ok"))


# ---------------------------------------------------------------------------

# ---------- Auto alerts from monitoring ----------
_alert_thread: Optional[threading.Thread] = None
_alert_stop = threading.Event()
_seen_alert_ids: set = set()


def start_alert_watcher() -> dict:
    global _alert_thread
    if _alert_thread and _alert_thread.is_alive():
        return {"ok": True, "running": True}
    _alert_stop.clear()
    _alert_thread = threading.Thread(target=_alert_loop, name="bale-dc-alerts", daemon=True)
    _alert_thread.start()
    return {"ok": True, "running": True}


def _alert_loop() -> None:
    while not _alert_stop.is_set():
        try:
            _check_monitoring_alerts()
        except Exception:
            log.exception("alert loop")
        _alert_stop.wait(60)


def _check_monitoring_alerts() -> None:
    s = load_settings()
    if not s.get("enabled") or not s.get("auto_alerts", True):
        return
    token = (s.get("bot_token") or "").strip()
    chat_id = (s.get("default_chat_id") or "").strip()
    if not token or not chat_id:
        return
    try:
        from core import monitoring_core as mc
        alerts = mc.load_table("alerts")
    except Exception:
        return
    if alerts is None or getattr(alerts, "empty", True):
        return

    new_rows = []
    for _, row in alerts.iterrows():
        aid = str(row.get("alert_id") or "")
        status = str(row.get("status") or "").upper()
        severity = str(row.get("severity") or "").upper()
        if status in ("RESOLVED", "CLOSED", "OK", "CLEAR"):
            continue
        # OPEN / ACKNOWLEDGED / CRITICAL
        if status not in ("OPEN", "ACKNOWLEDGED", "FIRING", "ACTIVE") and severity not in (
            "CRITICAL", "HIGH", "MAJOR", "WARNING",
        ):
            # still notify unknown non-resolved
            if status == "":
                continue
        key = aid or f"{row.get('device_id')}|{row.get('metric')}|{row.get('message')}|{row.get('last_seen_at') or row.get('created_at')}"
        if key in _seen_alert_ids:
            continue
        _seen_alert_ids.add(key)
        new_rows.append(row)

    if len(_seen_alert_ids) > 3000:
        # trim
        keep = list(_seen_alert_ids)[-1500:]
        _seen_alert_ids.clear()
        _seen_alert_ids.update(keep)

    if not new_rows:
        return

    lines = [f"🚨 هشدار مانیتورینگ ({len(new_rows)} مورد جدید)", "────────────────"]
    for row in new_rows[:15]:
        lines.append(
            f"• {row.get('device_name') or row.get('device_id') or '?'}\n"
            f"  {row.get('metric') or ''} | {row.get('status') or ''}\n"
            f"  {str(row.get('message') or row.get('detail') or '')[:120]}"
        )
    send_message(token, chat_id, "\n".join(lines))



# Background runner (uses bale_bot process_update logic via import)
# ---------------------------------------------------------------------------
def _poll_loop():
    """حلقه long-polling — در thread جدا اجرا می‌شود"""
    from bale_bot import process_update  # local import to avoid circulars at module load

    _set_status(running=True, last_error=None, started_at=time.time())
    log.info("Bale bot polling started")

    while not _stop_event.is_set():
        s = load_settings()
        token = (s.get("bot_token") or "").strip()
        if not token or not s.get("enabled") or not s.get("polling", True):
            _set_status(running=False, last_error="disabled or no token")
            break

        # expose token to bale_bot module
        import bale_bot as bb
        bb.BALE_TOKEN = token
        bb.BASE_URL = f"https://tapi.bale.ai/bot{token}"

        offset = s.get("last_offset")
        try:
            params: dict[str, Any] = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            res = bale_api(token, "getUpdates", **params)
            if not res.get("ok"):
                _set_status(last_error=res.get("description", "getUpdates failed"))
                time.sleep(3)
                continue

            updates = res.get("result") or []
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    process_update(u)
                except Exception:
                    log.exception("process_update error")
            if offset is not None:
                save_settings({"last_offset": offset})
        except Exception as e:
            _set_status(last_error=str(e))
            log.exception("poll loop error")
            time.sleep(3)

    _set_status(running=False)
    log.info("Bale bot polling stopped")


def start_runner() -> dict:
    global _runner_thread
    s = load_settings()
    token = (s.get("bot_token") or "").strip()
    if not token:
        return {"ok": False, "error": "توکن تنظیم نشده"}
    check = check_token(token)
    if not check.get("ok"):
        return {"ok": False, "error": check.get("description", "توکن نامعتبر")}

    with _lock:
        if _runner_thread and _runner_thread.is_alive():
            _set_status(bot_username=check.get("username"), bot_id=check.get("id"))
            return {"ok": True, "message": "قبلاً در حال اجراست", "status": get_status()}

        _stop_event.clear()
        save_settings({"enabled": True, "polling": True})
        _set_status(
            bot_username=check.get("username"),
            bot_id=check.get("id"),
            last_error=None,
        )
        _runner_thread = threading.Thread(target=_poll_loop, name="bale-poll", daemon=True)
        _runner_thread.start()

    start_alert_watcher()
    return {"ok": True, "message": "ربات شروع شد", "status": get_status(), "bot": check}


def stop_runner() -> dict:
    _stop_event.set()
    save_settings({"enabled": False})
    t = _runner_thread
    if t and t.is_alive():
        t.join(timeout=5)
    _set_status(running=False)
    return {"ok": True, "message": "ربات متوقف شد", "status": get_status()}


def ensure_started_if_configured() -> None:
    """در استارت اپ فراخوانی می‌شود"""
    s = load_settings()
    if s.get("enabled") and s.get("bot_token") and s.get("polling", True):
        log.info("Auto-starting Bale bot from saved settings...")
        result = start_runner()
        log.info("Auto-start result: %s", result)
        if s.get("auto_alerts", True):
            start_alert_watcher()
