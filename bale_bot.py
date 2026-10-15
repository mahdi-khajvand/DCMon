#!/usr/bin/env python3
"""
DC Monitor Pro — Bale Bot (نسخه بصری)
نمایش رنگی، نمودار و کارت‌های زیبا برای تمام قابلیت‌های نسخه وب.

اجرا (ویندوز PowerShell):
  $env:BALE_BOT_TOKEN = "TOKEN"
  python bale_bot.py

اجرا (لینوکس/مک):
  export BALE_BOT_TOKEN="TOKEN"
  python bale_bot.py
"""
from __future__ import annotations

import html
import re
import re
import io
import logging
import os
import time
from typing import Any, Optional

import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

from core import auth, inventory, connections
from core import monitoring_core as mc

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _resolve_token() -> str:
    t = os.environ.get("BALE_BOT_TOKEN", "").strip()
    if t:
        return t
    try:
        from core.bale_integration import load_settings
        return str(load_settings().get("bot_token") or "").strip()
    except Exception:
        return ""


BALE_TOKEN = _resolve_token()
BASE_URL = f"https://tapi.bale.ai/bot{BALE_TOKEN}" if BALE_TOKEN else ""
POLL_TIMEOUT = 30
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bale_bot")

SESSIONS: dict[int, dict] = {}

# رنگ‌های تم تیره/نئون
COLORS = {
    "bg": "#0f172a",
    "card": "#1e293b",
    "accent": "#8b5cf6",
    "blue": "#3b82f6",
    "green": "#22c55e",
    "red": "#ef4444",
    "amber": "#f59e0b",
    "cyan": "#06b6d4",
    "text": "#e2e8f0",
    "muted": "#94a3b8",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def api(method: str, **params) -> dict:
    if not BALE_TOKEN:
        raise RuntimeError("BALE_BOT_TOKEN تنظیم نشده است")
    url = f"{BASE_URL}/{method}"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, json=params)
            data = r.json()
            if not data.get("ok"):
                log.warning("API error %s: %s", method, data)
            return data
    except Exception as e:
        log.exception("API call failed: %s", method)
        return {"ok": False, "description": str(e)}


def _strip_tags(text: str) -> str:
    """Remove any HTML-like tags so Bale never shows raw <b> etc."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]*>", "", s)
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    s = s.replace("&quot;", '"').replace("&#39;", "'")
    # leftover broken tags like b> or /b>
    s = re.sub(r"</?[a-zA-Z][^\s<>]*>", "", s)
    s = re.sub(r"(?i)\b(?:/?b|/?code|/?pre|/?i|/?strong)\b>", "", s)
    return s.strip()


def send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "",  # ignored — Bale HTML is unreliable
) -> Optional[int]:
    clean = _strip_tags(text)
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": clean[:4000],
        "disable_web_page_preview": True,
    }
    # never send parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = api("sendMessage", **payload)
    if res.get("ok"):
        return res["result"]["message_id"]
    return None


def send_photo(
    chat_id: int,
    photo_bytes: bytes,
    caption: str = "",
    reply_markup: Optional[dict] = None,
) -> Optional[int]:
    """ارسال تصویر (نمودار) به بله"""
    url = f"{BASE_URL}/sendPhoto"
    files = {"photo": ("chart.png", photo_bytes, "image/png")}
    data: dict[str, Any] = {"chat_id": str(chat_id), "caption": _strip_tags(caption)[:1024]}
    if reply_markup:
        import json
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, data=data, files=files)
            res = r.json()
            if res.get("ok"):
                return res["result"]["message_id"]
            log.warning("sendPhoto failed: %s", res)
    except Exception:
        log.exception("sendPhoto error")
    return None


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": _strip_tags(text)[:4000],
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = api("editMessageText", **payload)
    return bool(res.get("ok"))


def answer_callback(callback_query_id: str, text: str = "", show_alert: bool = False) -> None:
    api("answerCallbackQuery", callback_query_id=callback_query_id, text=text, show_alert=show_alert)


def get_updates(offset: Optional[int] = None) -> list:
    params: dict[str, Any] = {"timeout": POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    res = api("getUpdates", **params)
    if res.get("ok"):
        return res.get("result", [])
    return []


# ---------------------------------------------------------------------------
# Visual helpers
# ---------------------------------------------------------------------------
def esc(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def progress_bar(value: float, max_val: float = 100, width: int = 10) -> str:
    """نوار پیشرفت با ایموجی"""
    if max_val <= 0:
        pct = 0
    else:
        pct = max(0, min(100, (value / max_val) * 100))
    filled = int(round(pct / 100 * width))
    empty = width - filled
    if pct >= 85:
        color = "🔴"
    elif pct >= 60:
        color = "🟡"
    else:
        color = "🟢"
    bar = "▓" * filled + "░" * empty
    return f"{color} {bar} {pct:.0f}%"


def status_emoji(status: str) -> str:
    s = str(status).upper()
    if s == "UP":
        return "🟢"
    if s == "DOWN":
        return "🔴"
    if s in ("OPEN", "CRITICAL"):
        return "🚨"
    if s == "ACKNOWLEDGED":
        return "🟡"
    return "⚪"


def fig_to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def style_ax(ax, title: str = ""):
    ax.set_facecolor(COLORS["card"])
    ax.tick_params(colors=COLORS["muted"], labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(COLORS["muted"])
        spine.set_linewidth(0.5)
    if title:
        ax.set_title(title, color=COLORS["text"], fontsize=12, fontweight="bold", pad=10)


def chart_dashboard(inv: dict, up: int, down: int, alerts: int, conn: int) -> bytes:
    """نمودار داشبورد اصلی"""
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8), facecolor=COLORS["bg"])
    fig.suptitle("DC Monitor Pro — Dashboard", color=COLORS["text"], fontsize=14, fontweight="bold", y=1.02)

    # 1) Inventory donut
    ax = axes[0]
    style_ax(ax, "Inventory Units")
    used = inv.get("used_units", 0)
    free = inv.get("free_units", 0)
    if used + free == 0:
        used, free = 1, 0
    wedges, texts, autotexts = ax.pie(
        [used, free],
        labels=["Used", "Free"],
        colors=[COLORS["accent"], COLORS["cyan"]],
        autopct="%1.0f%%",
        startangle=90,
        wedgeprops=dict(width=0.45, edgecolor=COLORS["bg"]),
        textprops=dict(color=COLORS["text"], fontsize=9),
    )
    for at in autotexts:
        at.set_color(COLORS["bg"])
        at.set_fontweight("bold")
    ax.text(0, 0, f"{inv.get('racks', 0)}\nRacks", ha="center", va="center",
            color=COLORS["text"], fontsize=11, fontweight="bold")

    # 2) Device status
    ax = axes[1]
    style_ax(ax, "Device Status")
    total_mon = up + down
    if total_mon == 0:
        up, down = 1, 0
    bars = ax.bar(["UP", "DOWN"], [up, down], color=[COLORS["green"], COLORS["red"]], width=0.5, edgecolor=COLORS["bg"])
    ax.set_ylim(0, max(up, down, 1) * 1.3)
    ax.set_ylabel("Count", color=COLORS["muted"], fontsize=8)
    for b, v in zip(bars, [up, down]):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.1, str(v),
                ha="center", va="bottom", color=COLORS["text"], fontweight="bold", fontsize=11)

    # 3) KPI cards style
    ax = axes[2]
    style_ax(ax, "Key Metrics")
    ax.axis("off")
    metrics = [
        ("Racks", inv.get("racks", 0), COLORS["blue"]),
        ("Devices", inv.get("devices", 0), COLORS["accent"]),
        ("Alerts", alerts, COLORS["red"] if alerts else COLORS["green"]),
        ("Links", conn, COLORS["cyan"]),
    ]
    for i, (label, val, color) in enumerate(metrics):
        y = 0.85 - i * 0.22
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.05, y - 0.08), 0.9, 0.18,
            boxstyle="round,pad=0.02,rounding_size=0.05",
            facecolor=COLORS["bg"], edgecolor=color, linewidth=1.5, transform=ax.transAxes,
        ))
        ax.text(0.12, y, label, transform=ax.transAxes, color=COLORS["muted"], fontsize=9, va="center")
        ax.text(0.88, y, str(val), transform=ax.transAxes, color=color, fontsize=14, fontweight="bold",
                va="center", ha="right")

    plt.tight_layout()
    return fig_to_bytes(fig)


def chart_monitoring(snaps: pd.DataFrame) -> bytes:
    """نمودار مانیتورینگ CPU/MEM"""
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), facecolor=COLORS["bg"])
    fig.suptitle("Monitoring Overview", color=COLORS["text"], fontsize=13, fontweight="bold")

    df = snaps.copy() if snaps is not None else pd.DataFrame()
    if df.empty:
        for ax in axes:
            style_ax(ax)
            ax.text(0.5, 0.5, "No data", ha="center", va="center", color=COLORS["muted"], transform=ax.transAxes)
        return fig_to_bytes(fig)

    if "display_name" in df.columns:
        names = df["display_name"].astype(str).tolist()
    elif "device_id" in df.columns:
        names = df["device_id"].astype(str).tolist()
    else:
        names = [str(i) for i in range(len(df))]
    names = [n[:14] + "..." if len(n) > 14 else n for n in names]

    if "cpu_pct" in df.columns:
        cpu = pd.to_numeric(df["cpu_pct"], errors="coerce").fillna(0).tolist()
    else:
        cpu = [0.0] * len(df)
    if "mem_pct" in df.columns:
        mem = pd.to_numeric(df["mem_pct"], errors="coerce").fillna(0).tolist()
    else:
        mem = [0.0] * len(df)

    n = min(len(names), 12)
    names, cpu, mem = names[:n], cpu[:n], mem[:n]
    y = np.arange(n)

    # CPU
    ax = axes[0]
    style_ax(ax, "CPU %")
    colors_cpu = [COLORS["red"] if v >= 85 else COLORS["amber"] if v >= 60 else COLORS["green"] for v in cpu]
    ax.barh(y, cpu, color=colors_cpu, height=0.6, edgecolor=COLORS["bg"])
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8, color=COLORS["text"])
    ax.set_xlim(0, 100)
    ax.axvline(80, color=COLORS["red"], linestyle="--", alpha=0.5, linewidth=1)
    ax.invert_yaxis()

    # MEM
    ax = axes[1]
    style_ax(ax, "Memory %")
    colors_mem = [COLORS["red"] if v >= 85 else COLORS["amber"] if v >= 60 else COLORS["cyan"] for v in mem]
    ax.barh(y, mem, color=colors_mem, height=0.6, edgecolor=COLORS["bg"])
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8, color=COLORS["text"])
    ax.set_xlim(0, 100)
    ax.axvline(80, color=COLORS["red"], linestyle="--", alpha=0.5, linewidth=1)
    ax.invert_yaxis()

    plt.tight_layout()
    return fig_to_bytes(fig)


def chart_rack_usage(racks: pd.DataFrame, placements: pd.DataFrame, devices: pd.DataFrame) -> bytes:
    """نمودار استفاده از رک‌ها"""
    fig, ax = plt.subplots(figsize=(9, 4.5), facecolor=COLORS["bg"])
    style_ax(ax, "Rack Utilization")

    if racks.empty:
        ax.text(0.5, 0.5, "No racks", ha="center", va="center", color=COLORS["muted"], transform=ax.transAxes)
        return fig_to_bytes(fig)

    rows = []
    for _, r in racks.iterrows():
        rid = int(r["rack_id"])
        total = int(r.get("total_units") or 42)
        pl = placements[placements["rack_id"].astype(int) == rid] if not placements.empty else pd.DataFrame()
        used = 0
        if not pl.empty and not devices.empty:
            m = pl.merge(devices[["device_id", "units_used"]], on="device_id", how="left")
            used = int(pd.to_numeric(m["units_used"], errors="coerce").fillna(0).sum())
        rows.append({
            "name": str(r.get("rack_name") or rid)[:16],
            "used": used,
            "free": max(0, total - used),
            "total": total,
            "pct": (used / total * 100) if total else 0,
        })

    rows = sorted(rows, key=lambda x: -x["pct"])[:15]
    names = [r["name"] for r in rows]
    used_vals = [r["used"] for r in rows]
    free_vals = [r["free"] for r in rows]
    y = np.arange(len(names))

    ax.barh(y, used_vals, color=COLORS["accent"], height=0.65, label="Used", edgecolor=COLORS["bg"])
    ax.barh(y, free_vals, left=used_vals, color=COLORS["cyan"], height=0.65, label="Free", alpha=0.7, edgecolor=COLORS["bg"])
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=9, color=COLORS["text"])
    ax.invert_yaxis()
    ax.legend(facecolor=COLORS["card"], edgecolor=COLORS["muted"], labelcolor=COLORS["text"], fontsize=8)
    ax.set_xlabel("Units", color=COLORS["muted"], fontsize=9)

    for i, r in enumerate(rows):
        ax.text(r["total"] + 0.5, i, f"{r['pct']:.0f}%", va="center", color=COLORS["muted"], fontsize=8)

    plt.tight_layout()
    return fig_to_bytes(fig)


def chart_alerts(alerts: pd.DataFrame) -> bytes:
    """نمودار آلرت‌ها"""
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), facecolor=COLORS["bg"])
    fig.suptitle("Alerts Overview", color=COLORS["text"], fontsize=13, fontweight="bold")

    if alerts.empty:
        for ax in axes:
            style_ax(ax)
            ax.text(0.5, 0.5, "No alerts ✅", ha="center", va="center", color=COLORS["green"], transform=ax.transAxes)
        return fig_to_bytes(fig)

    # status pie
    ax = axes[0]
    style_ax(ax, "By Status")
    status_counts = alerts["status"].astype(str).value_counts()
    colors_map = {"OPEN": COLORS["red"], "ACKNOWLEDGED": COLORS["amber"], "CLOSED": COLORS["green"], "RESOLVED": COLORS["cyan"]}
    cols = [colors_map.get(s, COLORS["muted"]) for s in status_counts.index]
    ax.pie(status_counts.values, labels=status_counts.index, colors=cols, autopct="%1.0f%%",
           textprops=dict(color=COLORS["text"], fontsize=9),
           wedgeprops=dict(edgecolor=COLORS["bg"], linewidth=2))

    # severity / metric bar
    ax = axes[1]
    style_ax(ax, "By Metric")
    if "metric" in alerts.columns:
        mc_counts = alerts["metric"].astype(str).value_counts().head(6)
        ax.barh(mc_counts.index.astype(str), mc_counts.values, color=COLORS["accent"], edgecolor=COLORS["bg"])
        ax.invert_yaxis()
        ax.tick_params(axis="y", labelsize=8)
    else:
        ax.text(0.5, 0.5, "—", ha="center", va="center", color=COLORS["muted"], transform=ax.transAxes)

    plt.tight_layout()
    return fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------
def kb_main(sess: Optional[dict] = None) -> dict:
    rows = [
        [{"text": "📊 داشبورد", "callback_data": "dash"}],
        [
            {"text": "🏢 دیتاسنترها", "callback_data": "dcs"},
            {"text": "🗄️ رک‌ها", "callback_data": "racks"},
        ],
        [
            {"text": "🖥️ دستگاه‌ها", "callback_data": "devices"},
            {"text": "📍 استقرار", "callback_data": "placements"},
        ],
        [
            {"text": "🔗 توپولوژی", "callback_data": "topology"},
            {"text": "🔌 اتصالات", "callback_data": "connections"},
        ],
        [
            {"text": "📡 مانیتورینگ", "callback_data": "mon_menu"},
            {"text": "🚨 آلرت‌ها", "callback_data": "mon_alerts"},
        ],
        [
            {"text": "🔍 دیسکاوری", "callback_data": "discovery"},
        ],
    ]
    if sess and auth.is_admin(sess):
        rows.append([{"text": "👥 کاربران", "callback_data": "users"}])
    rows.append([{"text": "🚪 خروج", "callback_data": "logout"}])
    return {"inline_keyboard": rows}


def kb_mon() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "📡 دستگاه‌ها + نمودار", "callback_data": "mon_devices"}],
            [{"text": "📈 پرفورمنس", "callback_data": "mon_perf"}],
            [{"text": "🚨 آلرت‌ها + نمودار", "callback_data": "mon_alerts"}],
            [{"text": "🪤 SNMP Traps", "callback_data": "mon_traps"}],
            [{"text": "◀️ بازگشت", "callback_data": "main"}],
        ]
    }


def kb_back(to: str = "main") -> dict:
    return {"inline_keyboard": [[{"text": "◀️ بازگشت", "callback_data": to}]]}


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
def get_state(chat_id: int) -> dict:
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = {"token": None, "sess": None, "state": "idle", "tmp": {}}
    return SESSIONS[chat_id]


def require_login(chat_id: int) -> Optional[dict]:
    st = get_state(chat_id)
    if not st.get("token"):
        return None
    sess = auth.resolve_token(st["token"])
    if not sess:
        st["token"] = None
        st["sess"] = None
        return None
    st["sess"] = sess
    return sess


def scoped_inventory(sess: dict):
    racks, devices, placements = inventory.load_inventory()
    return inventory.apply_dc_scope(racks, devices, placements, sess.get("allowed_dcs"))


# ---------------------------------------------------------------------------
# Feature handlers (visual)
# ---------------------------------------------------------------------------
def handle_dashboard(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    racks, devices, placements = scoped_inventory(sess)
    inv = inventory.inventory_summary(racks, devices, placements)

    mc.sync_inventory_devices(devices)
    mon = mc.list_monitoring_devices()
    snaps = mc.build_snapshot_table(mon)
    if not snaps.empty:
        mc.sync_alerts(snaps)
    alerts = mc.load_table("alerts")

    up = int((snaps["status"] == "UP").sum()) if not snaps.empty else 0
    down = int((snaps["status"] == "DOWN").sum()) if not snaps.empty else 0
    active_alerts = (
        int(alerts["status"].isin(["OPEN", "ACKNOWLEDGED"]).sum()) if not alerts.empty else 0
    )
    try:
        conn_df = connections.load_connections()
        conn_total = len(conn_df) if conn_df is not None and not conn_df.empty else 0
    except Exception:
        conn_total = 0

    # نمودار
    try:
        img = chart_dashboard(inv, up, down, active_alerts, conn_total)
        send_photo(chat_id, img, caption="📊 <b>داشبورد زنده</b>")
    except Exception:
        log.exception("dashboard chart failed")

    used_pct = (inv["used_units"] / inv["total_units"] * 100) if inv["total_units"] else 0
    text = (
        f"╔══════════════════════╗\n"
        f"║  ⚡ <b>DC MONITOR PRO</b>  ║\n"
        f"╚══════════════════════╝\n\n"
        f"👤 <b>{esc(sess['user'])}</b> · <code>{esc(sess['role'])}</code>\n\n"
        f"<b>📦 Inventory</b>\n"
        f"🗄️ رک‌ها: <b>{inv['racks']}</b>\n"
        f"🖥️ دستگاه‌ها: <b>{inv['devices']}</b>\n"
        f"📍 استقرار: <b>{inv['placements']}</b>\n"
        f"📐 یونیت: {progress_bar(used_pct)}\n"
        f"   کل <b>{inv['total_units']}</b> · استفاده <b>{inv['used_units']}</b> · آزاد <b>{inv['free_units']}</b>\n\n"
        f"<b>📡 Monitoring</b>\n"
        f"🟢 UP: <b>{up}</b>   🔴 DOWN: <b>{down}</b>\n"
        f"🚨 آلرت فعال: <b>{active_alerts}</b>\n\n"
        f"<b>🔌 Connections:</b> <b>{conn_total}</b>"
    )
    send_message(chat_id, text, kb_main(sess))


def handle_dcs(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    dcs, links = inventory.load_datacenters()
    if dcs.empty:
        text = "🏢 هیچ دیتاسنتری ثبت نشده."
    else:
        lines = ["🏢 <b>دیتاسنترها</b>\n"]
        for _, r in dcs.iterrows():
            lines.append(
                f"┌─────────────────\n"
                f"│ 🏛️ <b>{esc(r.get('datacenter_name'))}</b>\n"
                f"│ 🆔 ID: <code>{esc(r.get('datacenter_id'))}</code>\n"
                f"│ 📍 {esc(r.get('location') or '—')}\n"
                f"│ 📝 {esc(r.get('description') or '—')}\n"
                f"└─────────────────"
            )
        if not links.empty:
            lines.append("\n🔗 <b>لینک‌های DCI</b>")
            for _, l in links.head(15).iterrows():
                st = status_emoji(l.get("status"))
                lines.append(
                    f"{st} <code>{esc(l.get('source_dc_id'))}</code> ⇄ "
                    f"<code>{esc(l.get('destination_dc_id'))}</code> · "
                    f"{esc(l.get('link_type'))} · <b>{esc(l.get('capacity_gbps'))}G</b>"
                )
        text = "\n".join(lines)
    send_message(chat_id, text, kb_back("main"))


def handle_racks(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    racks, devices, placements = scoped_inventory(sess)
    if racks.empty:
        send_message(chat_id, "🗄️ هیچ رکی در محدوده دسترسی شما نیست.", kb_back("main"))
        return

    try:
        img = chart_rack_usage(racks, placements, devices)
        send_photo(chat_id, img, caption="🗄️ <b>نمودار استفاده از رک‌ها</b>")
    except Exception:
        log.exception("rack chart failed")

    lines = ["🗄️ <b>لیست رک‌ها</b>\n"]
    for _, r in racks.head(25).iterrows():
        rid = int(r["rack_id"])
        total = int(r.get("total_units") or 42)
        pl = placements[placements["rack_id"].astype(int) == rid] if not placements.empty else pd.DataFrame()
        used = 0
        if not pl.empty and not devices.empty:
            m = pl.merge(devices[["device_id", "units_used"]], on="device_id", how="left")
            used = int(pd.to_numeric(m["units_used"], errors="coerce").fillna(0).sum())
        pct = (used / total * 100) if total else 0
        lines.append(
            f"• <b>{esc(r.get('rack_name'))}</b> "
            f"<code>#{rid}</code> · DC={esc(r.get('datacenter_id'))}\n"
            f"  {progress_bar(pct)}  ({used}/{total}U)"
        )
    markup = {
        "inline_keyboard": [
            [{"text": "🔎 جزئیات رک (ID بفرست)", "callback_data": "rack_detail"}],
            [{"text": "◀️ بازگشت", "callback_data": "main"}],
        ]
    }
    send_message(chat_id, "\n".join(lines), markup)


def handle_rack_detail(chat_id: int, sess: dict, rack_id: int) -> None:
    racks, devices, placements = scoped_inventory(sess)
    rack = racks[racks["rack_id"].astype(int) == int(rack_id)]
    if rack.empty:
        send_message(chat_id, f"❌ رک <code>{rack_id}</code> یافت نشد.", kb_back("racks"))
        return
    r = rack.iloc[0]
    total = int(r.get("total_units") or 42)
    pl = placements[placements["rack_id"].astype(int) == int(rack_id)]

    # نقشه یونیت بصری
    occupied = {}
    if not pl.empty and not devices.empty:
        merged = pl.merge(
            devices[["device_id", "device_name", "model", "units_used"]],
            on="device_id", how="left",
        )
        for _, p in merged.iterrows():
            for u in range(int(p["start_unit"]), int(p["end_unit"]) + 1):
                occupied[u] = p

    lines = [
        f"🗄️ <b>رک {esc(r.get('rack_name'))}</b>",
        f"🆔 <code>{esc(r.get('rack_id'))}</code> · DC <code>{esc(r.get('datacenter_id'))}</code>",
        f"📐 ظرفیت: <b>{total}U</b>",
        f"📝 {esc(r.get('description') or '—')}",
        "",
        "<b>🗺️ نقشه یونیت‌ها</b>",
        "┌" + "─" * 28 + "┐",
    ]
    # از بالا به پایین (مثل رک واقعی)
    for u in range(total, 0, -1):
        if u in occupied:
            p = occupied[u]
            name = str(p.get("device_name") or "?")[:18]
            lines.append(f"│ U{u:02d} ████ <b>{esc(name)}</b>")
        else:
            lines.append(f"│ U{u:02d} ░░░░ <i>خالی</i>")
    lines.append("└" + "─" * 28 + "┘")
    used = len(occupied)
    lines.append(f"\n{progress_bar(used / total * 100 if total else 0)}  ({used}/{total}U)")
    send_message(chat_id, "\n".join(lines), kb_back("racks"))


def handle_devices(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    _, devices, _ = scoped_inventory(sess)
    if devices.empty:
        send_message(chat_id, "🖥️ هیچ دستگاهی نیست.", kb_back("main"))
        return
    lines = ["🖥️ <b>دستگاه‌ها</b>\n"]
    for _, d in devices.head(30).iterrows():
        lines.append(
            f"┌ <b>{esc(d.get('device_name'))}</b>\n"
            f"│ 🆔 <code>{esc(d.get('device_id'))}</code> · {esc(d.get('model') or '—')}\n"
            f"│ 📐 {esc(d.get('units_used'))}U · 🔌 net={esc(d.get('network_ports'))} "
            f"pwr={esc(d.get('power_ports'))} fiber={esc(d.get('fiber_ports'))}\n"
            f"│ 🏢 DC={esc(d.get('datacenter_id'))}\n"
            f"└────────"
        )
    send_message(chat_id, "\n".join(lines), kb_back("main"))


def handle_placements(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    racks, devices, placements = scoped_inventory(sess)
    if placements.empty:
        send_message(chat_id, "📍 هیچ استقراری ثبت نشده.", kb_back("main"))
        return
    merged = placements.merge(
        racks[["rack_id", "rack_name"]], on="rack_id", how="left"
    ).merge(
        devices[["device_id", "device_name"]], on="device_id", how="left"
    )
    lines = ["📍 <b>استقرار دستگاه‌ها</b>\n"]
    for _, p in merged.head(30).iterrows():
        lines.append(
            f"🔹 <b>{esc(p.get('device_name'))}</b>\n"
            f"   ➜ 🗄️ <b>{esc(p.get('rack_name'))}</b>  "
            f"U{int(p.get('start_unit'))}–{int(p.get('end_unit'))}"
        )
    send_message(chat_id, "\n".join(lines), kb_back("main"))


def handle_topology(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    dcs, links = inventory.load_datacenters()
    lines = ["🔗 <b>توپولوژی دیتاسنترها</b>\n"]
    if not dcs.empty:
        for _, r in dcs.iterrows():
            lines.append(f"🏛️ <b>{esc(r.get('datacenter_name'))}</b> <code>#{esc(r.get('datacenter_id'))}</code>")
    if not links.empty:
        lines.append("\n<b>لینک‌ها</b>")
        for _, l in links.iterrows():
            st = status_emoji(l.get("status"))
            lines.append(
                f"{st} {esc(l.get('source_dc_id'))} ════ {esc(l.get('destination_dc_id'))}\n"
                f"   {esc(l.get('link_type'))} · <b>{esc(l.get('capacity_gbps'))} Gbps</b> · {esc(l.get('status'))}"
            )
    else:
        lines.append("\nلینک DCI ثبت نشده.")
    send_message(chat_id, "\n".join(lines), kb_back("main"))


def handle_connections(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    try:
        df = connections.load_connections()
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        send_message(chat_id, "🔌 هیچ اتصالی ثبت نشده.", kb_back("main"))
        return
    lines = [f"🔌 <b>اتصالات</b> ({len(df)} مورد)\n"]
    cols = [c for c in [
        "connection_id", "source_device", "source_port",
        "dest_device", "dest_port", "connection_type", "status"
    ] if c in df.columns]
    for _, r in df.head(25).iterrows():
        st = status_emoji(r.get("status", ""))
        src = esc(r.get("source_device", "?"))
        dst = esc(r.get("dest_device", "?"))
        lines.append(
            f"{st} <b>{src}</b>:{esc(r.get('source_port', ''))} "
            f"⟷ <b>{dst}</b>:{esc(r.get('dest_port', ''))}\n"
            f"   {esc(r.get('connection_type', ''))} · id=<code>{esc(r.get('connection_id', ''))}</code>"
        )
    send_message(chat_id, "\n".join(lines), kb_back("main"))


def handle_mon_devices(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    mon = mc.list_monitoring_devices()
    snaps = mc.build_snapshot_table(mon)
    if not snaps.empty:
        try:
            img = chart_monitoring(snaps)
            send_photo(chat_id, img, caption="📡 <b>وضعیت مانیتورینگ</b>")
        except Exception:
            log.exception("mon chart failed")

    if snaps.empty:
        send_message(chat_id, "📡 دستگاه مانیتورینگی وجود ندارد.", kb_back("mon_menu"))
        return

    lines = ["📡 <b>دستگاه‌های مانیتور</b>\n"]
    for _, r in snaps.head(20).iterrows():
        st = status_emoji(r.get("status", "?"))
        cpu = float(pd.to_numeric(r.get("cpu_pct", 0), errors="coerce") or 0)
        mem = float(pd.to_numeric(r.get("mem_pct", 0), errors="coerce") or 0)
        lines.append(
            f"{st} <b>{esc(r.get('display_name') or r.get('device_id'))}</b>\n"
            f"   🌐 {esc(r.get('host'))}\n"
            f"   CPU {progress_bar(cpu)}\n"
            f"   MEM {progress_bar(mem)}"
        )
    send_message(chat_id, "\n".join(lines), kb_back("mon_menu"))


def handle_mon_perf(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    mon = mc.list_monitoring_devices()
    snaps = mc.build_snapshot_table(mon)
    if snaps.empty:
        send_message(chat_id, "📈 داده‌ای نیست.", kb_back("mon_menu"))
        return
    try:
        img = chart_monitoring(snaps)
        send_photo(chat_id, img, caption="📈 <b>پرفورمنس</b>")
    except Exception:
        log.exception("perf chart failed")

    lines = ["📈 <b>جزئیات پرفورمنس</b>\n"]
    for _, r in snaps.head(15).iterrows():
        st = status_emoji(r.get("status"))
        lines.append(
            f"{st} <b>{esc(r.get('display_name') or r.get('device_id'))}</b>\n"
            f"   CPU: <b>{esc(r.get('cpu_pct', '—'))}%</b> · "
            f"MEM: <b>{esc(r.get('mem_pct', '—'))}%</b> · "
            f"Temp: <b>{esc(r.get('temp_c', '—'))}°C</b>"
        )
    send_message(chat_id, "\n".join(lines), kb_back("mon_menu"))


def handle_mon_alerts(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    alerts = mc.load_table("alerts")
    if alerts is None or alerts.empty:
        send_message(chat_id, "🚨 آلرتی ثبت نشده.", kb_back("mon_menu"))
        return

    # chart if possible
    try:
        img = chart_alerts(alerts)
        send_photo(chat_id, img, caption="نمودار آلرت‌ها")
    except Exception:
        log.exception("alert chart failed")

    status_col = "status" if "status" in alerts.columns else None
    if status_col:
        open_a = alerts[alerts[status_col].astype(str).str.upper().isin(["OPEN", "ACKNOWLEDGED", "FIRING", "ACTIVE"])]
    else:
        open_a = alerts

    lines = [f"🚨 آلرت‌های فعال ({len(open_a)})", "────────────────"]
    for _, a in open_a.head(20).iterrows():
        sev = str(a.get("severity") or a.get("level") or "?")
        st = str(a.get("status") or "")
        name = (
            a.get("device_name")
            or a.get("hostname")
            or a.get("monitoring_device_id")
            or a.get("device_id")
            or "?"
        )
        metric = a.get("metric_name") or a.get("metric") or ""
        val = a.get("value")
        thr = a.get("threshold")
        aid = a.get("alert_id") or a.get("id") or ""
        msg = str(a.get("message") or "")[:80]
        em = "🔴" if sev.upper() == "CRITICAL" else "🟡" if sev.upper() == "WARNING" else "🟠"
        lines.append(
            f"{em} {sev} [{st}]\n"
            f"   {name}\n"
            f"   {metric} = {val}  (حد={thr})\n"
            f"   {msg}\n"
            f"   id: {aid}"
        )
    if open_a.empty:
        lines.append("✅ آلرت فعالی نیست.")
    markup = {
        "inline_keyboard": [
            [{"text": "✅ Ack آلرت (ID بفرست)", "callback_data": "ack_alert"}],
            [{"text": "◀️ بازگشت", "callback_data": "mon_menu"}],
        ]
    }
    send_message(chat_id, "\n".join(lines), markup)



def handle_mon_traps(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    traps = mc.load_table("traps")
    if traps.empty:
        send_message(chat_id, "🪤 هیچ SNMP Trap ثبت نشده.", kb_back("mon_menu"))
        return
    lines = [f"🪤 <b>SNMP Traps</b> ({len(traps)} مورد)\n"]
    for _, t in traps.tail(15).iloc[::-1].iterrows():
        lines.append(
            f"🔹 {esc(t.get('received_at') or t.get('timestamp') or '')}\n"
            f"   🌐 {esc(t.get('source') or t.get('host'))}\n"
            f"   📋 {esc(str(t.get('oid') or t.get('trap_oid') or '')[:40])}\n"
            f"   {esc(str(t.get('message') or t.get('varbinds') or '')[:70])}"
        )
    send_message(chat_id, "\n".join(lines), kb_back("mon_menu"))


def handle_discovery(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    st = get_state(chat_id)
    st["state"] = "discovery_input"
    text = (
        "🔍 <b>Network Discovery</b>\n\n"
        "یک CIDR بفرستید:\n"
        "• پیش‌نمایش: <code>192.168.1.0/24</code>\n"
        "• ICMP واقعی: <code>icmp 192.168.1.0/24</code>"
    )
    send_message(chat_id, text, kb_back("main"))


def handle_users(chat_id: int, sess: dict, message_id: Optional[int] = None) -> None:
    if not auth.is_admin(sess):
        send_message(chat_id, "⛔ فقط ادمین.", kb_back("main"))
        return
    users = auth.load_users()
    lines = ["👥 <b>کاربران سیستم</b>\n"]
    for _, u in users.iterrows():
        active = "✅" if str(u.get("is_active")) in ("1", "True", "true") else "❌"
        role = str(u.get("role", ""))
        role_icon = "👑" if role == "admin" else "✏️" if role == "editor" else "👁️"
        lines.append(
            f"{active} {role_icon} <b>{esc(u.get('username'))}</b>\n"
            f"   role=<code>{esc(role)}</code> · DCs=<code>{esc(u.get('allowed_dcs'))}</code>"
        )
    send_message(chat_id, "\n".join(lines), kb_back("main"))


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def process_callback(chat_id: int, data: str, callback_id: str, message_id: int) -> None:
    answer_callback(callback_id)
    st = get_state(chat_id)

    if data == "logout":
        st["token"] = None
        st["sess"] = None
        st["state"] = "idle"
        st["tmp"] = {}
        edit_message(chat_id, message_id, "🚪 خارج شدید.\nدوباره /start بزنید.", None)
        return

    sess = require_login(chat_id)
    if not sess:
        edit_message(chat_id, message_id, "لطفاً ابتدا وارد شوید.\n/start", None)
        return

    # برای callbackها پیام جدید می‌فرستیم (چون نمودار داریم)
    if data == "main" or data == "dash":
        handle_dashboard(chat_id, sess)
    elif data == "dcs":
        handle_dcs(chat_id, sess)
    elif data == "racks":
        handle_racks(chat_id, sess)
    elif data == "rack_detail":
        st["state"] = "await_rack_id"
        send_message(chat_id, "🔎 شناسه رک (rack_id) را بفرستید:", kb_back("racks"))
    elif data == "devices":
        handle_devices(chat_id, sess)
    elif data == "placements":
        handle_placements(chat_id, sess)
    elif data == "topology":
        handle_topology(chat_id, sess)
    elif data == "connections":
        handle_connections(chat_id, sess)
    elif data == "mon_menu":
        send_message(chat_id, "📡 <b>منوی مانیتورینگ</b>\nیکی را انتخاب کنید:", kb_mon())
    elif data == "mon_devices":
        handle_mon_devices(chat_id, sess)
    elif data == "mon_perf":
        handle_mon_perf(chat_id, sess)
    elif data == "mon_alerts":
        handle_mon_alerts(chat_id, sess)
    elif data == "mon_traps":
        handle_mon_traps(chat_id, sess)
    elif data == "ack_alert":
        st["state"] = "await_alert_id"
        send_message(chat_id, "✅ شناسه آلرت را برای Ack بفرستید:", kb_back("mon_alerts"))
    elif data == "discovery":
        handle_discovery(chat_id, sess)
    elif data == "users":
        handle_users(chat_id, sess)
    else:
        send_message(chat_id, f"دستور ناشناخته: {esc(data)}", kb_main(sess))


def process_text(chat_id: int, text: str, from_user: dict) -> None:
    text = (text or "").strip()
    st = get_state(chat_id)

    if text.startswith("/start") or text.strip() in ("شروع", "start"):
        st["state"] = "login_user"
        st["tmp"] = {}
        st["token"] = None
        st["sess"] = None
        send_message(
            chat_id,
            f"╔══════════════════════╗\n"
            f"║  ⚡ <b>DC MONITOR PRO</b>  ║\n"
            f"║     ربات بله         ║\n"
            f"╚══════════════════════╝\n\n"
            f"سلام {esc(from_user.get('first_name') or '')} 👋\n\n"
            f"نام کاربری را بفرستید:\n"
            f"(مثال: <code>admin</code>)",
        )
        return

    if st["state"] == "login_user":
        st["tmp"]["username"] = text
        st["state"] = "login_pass"
        send_message(chat_id, "🔐 رمز عبور را بفرستید:")
        return

    if st["state"] == "login_pass":
        username = st["tmp"].get("username", "")
        result = auth.login(username, text)
        if not result:
            st["state"] = "login_user"
            st["tmp"] = {}
            send_message(chat_id, "❌ نام کاربری یا رمز اشتباه.\nدوباره نام کاربری را بفرستید:")
            return
        st["token"] = result["token"]
        st["sess"] = result
        st["state"] = "idle"
        st["tmp"] = {}
        send_message(
            chat_id,
            f"✅ خوش آمدید <b>{esc(result['user'])}</b>\n"
            f"نقش: <code>{esc(result['role'])}</code>\n\n"
            f"از منوی زیر استفاده کنید 👇",
            kb_main(result),
        )
        return

    sess = require_login(chat_id)
    if not sess:
        send_message(chat_id, "لطفاً با /start وارد شوید.")
        return

    if st["state"] == "await_rack_id":
        st["state"] = "idle"
        try:
            handle_rack_detail(chat_id, sess, int(text))
        except ValueError:
            send_message(chat_id, "شناسه نامعتبر. یک عدد بفرستید.", kb_back("racks"))
        return

    if st["state"] == "await_alert_id":
        st["state"] = "idle"
        try:
            mc.acknowledge_alert(text.strip(), sess["user"])
            send_message(chat_id, f"✅ آلرت <code>{esc(text)}</code> تایید شد.", kb_back("mon_alerts"))
        except Exception as e:
            send_message(chat_id, f"❌ خطا: {esc(e)}", kb_back("mon_alerts"))
        return

    if st["state"] == "discovery_input":
        st["state"] = "idle"
        cidr = text.strip()
        try:
            if cidr.lower().startswith("icmp "):
                cidr = cidr[5:].strip()
                send_message(chat_id, f"⏳ ICMP scan روی <code>{esc(cidr)}</code> ...")
                df = mc.discover_icmp(cidr, max_hosts=64)
            else:
                send_message(chat_id, f"⏳ پیش‌نمایش <code>{esc(cidr)}</code> ...")
                df = mc.preview_discovery(cidr)
            if df is None or df.empty:
                send_message(chat_id, "نتیجه‌ای یافت نشد.", kb_back("main"))
            else:
                lines = [f"🔍 <b>نتایج</b> ({len(df)} مورد)\n"]
                for _, r in df.head(25).iterrows():
                    alive = str(r.get("status") or r.get("alive") or "")
                    em = "🟢" if str(alive).lower() in ("up", "true", "1", "alive") else "🔴"
                    lines.append(
                        f"{em} <code>{esc(r.get('ip') or r.get('address'))}</code> "
                        f"{esc(r.get('hostname') or '')}"
                    )
                send_message(chat_id, "\n".join(lines), kb_back("main"))
        except Exception as e:
            send_message(chat_id, f"❌ خطا: {esc(e)}", kb_back("main"))
        return

    if text in ("منو", "menu", "/menu", "dashboard"):
        handle_dashboard(chat_id, sess)
        return

    send_message(chat_id, "از منوی زیر استفاده کنید 👇", kb_main(sess))


def process_update(update: dict) -> None:
    try:
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            data = cq.get("data") or ""
            process_callback(chat_id, data, cq["id"], cq["message"]["message_id"])
            return
        if "message" in update:
            msg = update["message"]
            process_text(msg["chat"]["id"], msg.get("text") or "", msg.get("from") or {})
    except Exception:
        log.exception("Error processing update %s", update.get("update_id"))


def main() -> None:
    if not BALE_TOKEN:
        print("=" * 60)
        print("  BALE_BOT_TOKEN تنظیم نشده!")
        print("  PowerShell:")
        print('    $env:BALE_BOT_TOKEN = "TOKEN"')
        print("    python bale_bot.py")
        print("=" * 60)
        raise SystemExit(1)

    inventory.ensure_data_files()
    mc.ensure_monitoring_storage()
    auth.ensure_users_file()

    me = api("getMe")
    if not me.get("ok"):
        log.error("توکن نامعتبر: %s", me)
        raise SystemExit(1)
    bot_user = me["result"]
    log.info("ربات آماده: @%s (id=%s)", bot_user.get("username"), bot_user.get("id"))

    offset = None
    log.info("long-polling ...")
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                process_update(u)
        except KeyboardInterrupt:
            log.info("توقف")
            break
        except Exception:
            log.exception("خطا در حلقه")
            time.sleep(3)


if __name__ == "__main__":
    main()
