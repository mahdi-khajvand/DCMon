"""Monitoring domain helpers for DC Monitor.

This module deliberately has no Streamlit dependency.  The UI can be replaced
without moving discovery, storage, SNMP probing, or demo telemetry logic.
Long-running polling and trap reception belong in separate worker processes;
the functions here only perform bounded, user-triggered operations.
"""

from __future__ import annotations

import hashlib
import ipaddress
import itertools
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
MONITORING_DIR = BASE_DIR / "data" / "monitoring"
SETTINGS_FILE = MONITORING_DIR / "settings.json"

TABLE_SCHEMAS = {
    "credentials": [
        "credential_id",
        "name",
        "version",
        "username",
        "security_level",
        "secret_ref",
        "notes",
        "is_active",
        "created_at",
    ],
    "templates": [
        "template_id",
        "name",
        "vendor",
        "device_type",
        "poll_interval_seconds",
        "oids_json",
        "is_active",
        "created_at",
    ],
    "devices": [
        "monitoring_device_id",
        "inventory_device_id",
        "display_name",
        "host",
        "vendor",
        "model",
        "datacenter_id",
        "network_ports",
        "template_id",
        "credential_id",
        "poll_mode",
        "enabled",
        "inventory_present",
        "last_poll_at",
        "last_poll_status",
        "last_error",
        "created_at",
    ],
    "alerts": [
        "alert_id",
        "dedupe_key",
        "monitoring_device_id",
        "device_name",
        "metric_name",
        "severity",
        "status",
        "value",
        "threshold",
        "message",
        "first_seen_at",
        "last_seen_at",
        "acknowledged_at",
        "acknowledged_by",
        "resolved_at",
    ],
    "traps": [
        "trap_id",
        "monitoring_device_id",
        "device_name",
        "source_address",
        "snmp_version",
        "enterprise_oid",
        "trap_oid",
        "severity",
        "message",
        "varbinds_json",
        "status",
        "received_at",
        "acknowledged_at",
        "acknowledged_by",
    ],
}

_WRITE_LOCK = threading.RLock()

DEFAULT_OIDS = {
    "generic": [
        {"name": "sysName", "oid": "1.3.6.1.2.1.1.5.0", "unit": ""},
        {"name": "sysObjectID", "oid": "1.3.6.1.2.1.1.2.0", "unit": ""},
        {"name": "uptime", "oid": "1.3.6.1.2.1.1.3.0", "unit": "ticks"},
        {"name": "ifOperStatus", "oid": "1.3.6.1.2.1.2.2.1.8", "unit": ""},
        {"name": "ifInOctets", "oid": "1.3.6.1.2.1.2.2.1.10", "unit": "octets"},
        {"name": "ifOutOctets", "oid": "1.3.6.1.2.1.2.2.1.16", "unit": "octets"},
    ],
    "cisco": [
        {"name": "sysName", "oid": "1.3.6.1.2.1.1.5.0", "unit": ""},
        {"name": "uptime", "oid": "1.3.6.1.2.1.1.3.0", "unit": "ticks"},
        {
            "name": "cpu_5min",
            "oid": "1.3.6.1.4.1.9.9.109.1.1.1.1.8",
            "unit": "%",
        },
        {
            "name": "memory_used",
            "oid": "1.3.6.1.4.1.9.9.48.1.1.1.5",
            "unit": "bytes",
        },
        {
            "name": "memory_free",
            "oid": "1.3.6.1.4.1.9.9.48.1.1.1.6",
            "unit": "bytes",
        },
        {"name": "ifOperStatus", "oid": "1.3.6.1.2.1.2.2.1.8", "unit": ""},
        {"name": "ifHCInOctets", "oid": "1.3.6.1.2.1.31.1.1.1.6", "unit": "octets"},
        {"name": "ifHCOutOctets", "oid": "1.3.6.1.2.1.31.1.1.1.10", "unit": "octets"},
    ],
}

DEFAULT_TEMPLATES = [
    {
        "template_id": "tpl-generic-snmp",
        "name": "Generic SNMP",
        "vendor": "Any",
        "device_type": "Network / Server",
        "poll_interval_seconds": 300,
        "oids_json": json.dumps(DEFAULT_OIDS["generic"]),
        "is_active": 1,
    },
    {
        "template_id": "tpl-cisco-ios",
        "name": "Cisco IOS / IOS-XE",
        "vendor": "Cisco",
        "device_type": "Router / Switch",
        "poll_interval_seconds": 300,
        "oids_json": json.dumps(DEFAULT_OIDS["cisco"]),
        "is_active": 1,
    },
    {
        "template_id": "tpl-server",
        "name": "Generic Server",
        "vendor": "Any",
        "device_type": "Server",
        "poll_interval_seconds": 300,
        "oids_json": json.dumps(DEFAULT_OIDS["generic"]),
        "is_active": 1,
    },
    {
        "template_id": "tpl-storage",
        "name": "Storage Appliance",
        "vendor": "Any",
        "device_type": "Storage",
        "poll_interval_seconds": 300,
        "oids_json": json.dumps(DEFAULT_OIDS["generic"]),
        "is_active": 1,
    },
]

DEFAULT_SETTINGS = {
    "default_poll_interval_seconds": 300,
    "default_timeout_seconds": 2,
    "default_retries": 1,
    "metric_retention_days": 90,
    "demo_telemetry_enabled": True,
    "trap_listener_host": "0.0.0.0",
    "trap_listener_port": 162,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table_path(table: str) -> Path:
    if table not in TABLE_SCHEMAS:
        raise ValueError(f"Unknown monitoring table: {table}")
    return MONITORING_DIR / f"{table}.csv"


def _atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.stem}-",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                df.to_csv(handle, index=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def ensure_monitoring_storage() -> None:
    MONITORING_DIR.mkdir(parents=True, exist_ok=True)
    for table, columns in TABLE_SCHEMAS.items():
        path = _table_path(table)
        if not path.exists():
            _atomic_write_csv(pd.DataFrame(columns=columns), path)

    templates = load_table("templates")
    if templates.empty:
        rows = []
        created_at = utc_now()
        for template in DEFAULT_TEMPLATES:
            rows.append({**template, "created_at": created_at})
        save_table("templates", pd.DataFrame(rows))

    credentials = load_table("credentials")
    if credentials.empty:
        save_table(
            "credentials",
            pd.DataFrame(
                [
                    {
                        "credential_id": "cred-snmp-cisco",
                        "name": "SNMP-Cisco",
                        "version": "v2c",
                        "username": "",
                        "security_level": "community",
                        "secret_ref": "",
                        "notes": "Metadata only; provide the community securely at probe/worker runtime.",
                        "is_active": 1,
                        "created_at": utc_now(),
                    }
                ]
            ),
        )

    if not SETTINGS_FILE.exists():
        save_settings(DEFAULT_SETTINGS)

    # Credential metadata is not itself secret, but keep the file private.
    try:
        os.chmod(_table_path("credentials"), 0o600)
    except OSError:
        pass


def load_table(table: str) -> pd.DataFrame:
    path = _table_path(table)
    columns = TABLE_SCHEMAS[table]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    return df[columns]


def save_table(table: str, df: pd.DataFrame) -> None:
    columns = TABLE_SCHEMAS[table]
    normalized = df.copy()
    for column in columns:
        if column not in normalized.columns:
            normalized[column] = ""
    _atomic_write_csv(normalized[columns].fillna(""), _table_path(table))


def load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULT_SETTINGS, **saved}


def save_settings(settings: Mapping[str, Any]) -> None:
    MONITORING_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**DEFAULT_SETTINGS, **dict(settings)}
    fd, temp_name = tempfile.mkstemp(
        prefix=".settings-",
        suffix=".tmp",
        dir=MONITORING_DIR,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, SETTINGS_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _stable_int(*parts: Any, modulo: int = 10_000) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:16], 16) % modulo


def _is_passive_asset(name: str, model: str) -> bool:
    text = f"{name} {model}".lower()
    return any(
        marker in text
        for marker in ("patch panel", "cable management", "organizer", "dae")
    )


def infer_vendor_model(name: str, model: str) -> tuple[str, str, str]:
    text = f"{name} {model}".lower()
    if "cisco" in text or "catalyst" in text:
        return "Cisco", model or "Cisco IOS device", "tpl-cisco-ios"
    if any(marker in text for marker in ("proliant", "poweredge", "server", "srv-")):
        vendor = "HPE" if any(marker in text for marker in ("hpe", "hp ", "proliant")) else "Dell"
        return vendor, model or "Server", "tpl-server"
    if any(marker in text for marker in ("storage", "storeever", "data domain", "unity", "tape")):
        return "Storage Vendor", model or "Storage Appliance", "tpl-storage"
    if any(marker in text for marker in ("switch", "router", "connectrix", "san-")):
        return "Network Vendor", model or "Network Device", "tpl-generic-snmp"
    return "Unknown", model or "Unknown", "tpl-generic-snmp"


def sync_inventory_devices(
    inventory_df: pd.DataFrame,
    include_passive: bool = False,
) -> dict[str, int]:
    """Idempotently create monitoring records for inventory assets."""
    ensure_monitoring_storage()
    devices = load_table("devices")
    now = utc_now()
    created = 0
    updated = 0
    seen_ids: set[str] = set()

    for _, row in inventory_df.iterrows():
        inventory_id = str(row.get("device_id", "")).strip()
        if not inventory_id:
            continue
        name = str(row.get("device_name", f"Device {inventory_id}")).strip()
        model = str(row.get("model", "")).strip()
        if not include_passive and _is_passive_asset(name, model):
            continue

        seen_ids.add(inventory_id)
        vendor, normalized_model, template_id = infer_vendor_model(name, model)
        match = devices["inventory_device_id"].astype(str) == inventory_id
        base_values = {
            "display_name": name,
            "vendor": vendor,
            "model": normalized_model,
            "datacenter_id": str(row.get("datacenter_id", "")),
            "network_ports": str(row.get("network_ports", 0)),
            "inventory_present": "1",
        }
        if match.any():
            index = devices[match].index[0]
            changed = False
            for key, value in base_values.items():
                if str(devices.at[index, key]) != str(value):
                    devices.at[index, key] = value
                    changed = True
            if changed:
                updated += 1
            continue

        new_row = {
            "monitoring_device_id": f"mon-inv-{inventory_id}",
            "inventory_device_id": inventory_id,
            **base_values,
            "host": "",
            "template_id": template_id,
            "credential_id": "",
            "poll_mode": "demo",
            "enabled": "1",
            "last_poll_at": "",
            "last_poll_status": "DEMO",
            "last_error": "",
            "created_at": now,
        }
        devices = pd.concat([devices, pd.DataFrame([new_row])], ignore_index=True)
        created += 1

    if not devices.empty:
        inventory_rows = devices["inventory_device_id"].astype(str).str.len() > 0
        missing = inventory_rows & ~devices["inventory_device_id"].astype(str).isin(seen_ids)
        if missing.any():
            devices.loc[missing, "inventory_present"] = "0"

    if created or updated:
        save_table("devices", devices)
    return {"created": created, "updated": updated, "total": len(devices)}


def list_monitoring_devices() -> pd.DataFrame:
    ensure_monitoring_storage()
    df = load_table("devices")
    if df.empty:
        return df
    for column in ("enabled", "inventory_present", "network_ports", "datacenter_id"):
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
    return df.sort_values(["display_name", "monitoring_device_id"]).reset_index(drop=True)


def add_monitoring_device(values: Mapping[str, Any]) -> str:
    ensure_monitoring_storage()
    host = str(values.get("host", "")).strip()
    name = str(values.get("display_name", "")).strip()
    if not host or not name:
        raise ValueError("Device name and management host are required.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Hostnames are valid as long as they contain no whitespace.
        if any(char.isspace() for char in host):
            raise ValueError("Management host is not a valid IP address or hostname.")

    devices = load_table("devices")
    if not devices.empty and devices["host"].astype(str).str.casefold().eq(host.casefold()).any():
        raise ValueError(f"A monitoring device with host {host} already exists.")
    device_id = f"mon-{uuid.uuid4().hex[:12]}"
    row = {
        "monitoring_device_id": device_id,
        "inventory_device_id": str(values.get("inventory_device_id", "")),
        "display_name": name,
        "host": host,
        "vendor": str(values.get("vendor", "Unknown")),
        "model": str(values.get("model", "Unknown")),
        "datacenter_id": str(values.get("datacenter_id", "")),
        "network_ports": str(values.get("network_ports", 24)),
        "template_id": str(values.get("template_id", "tpl-generic-snmp")),
        "credential_id": str(values.get("credential_id", "")),
        "poll_mode": str(values.get("poll_mode", "snmp")),
        "enabled": "1",
        "inventory_present": "0",
        "last_poll_at": "",
        "last_poll_status": "PENDING",
        "last_error": "",
        "created_at": utc_now(),
    }
    devices = pd.concat([devices, pd.DataFrame([row])], ignore_index=True)
    save_table("devices", devices)
    return device_id


def update_monitoring_device(device_id: str, patch: Mapping[str, Any]) -> None:
    devices = load_table("devices")
    match = devices["monitoring_device_id"].astype(str) == str(device_id)
    if not match.any():
        raise ValueError("Monitoring device was not found.")
    allowed = {
        "display_name",
        "host",
        "vendor",
        "model",
        "datacenter_id",
        "network_ports",
        "template_id",
        "credential_id",
        "poll_mode",
        "enabled",
    }
    index = devices[match].index[0]
    for key, value in patch.items():
        if key in allowed:
            devices.at[index, key] = str(value)
    save_table("devices", devices)


def save_credential_profile(values: Mapping[str, Any]) -> str:
    """Save credential metadata only; secrets remain outside the CSV store."""
    ensure_monitoring_storage()
    credentials = load_table("credentials")
    name = str(values.get("name", "")).strip()
    if not name:
        raise ValueError("Credential profile name is required.")
    if credentials["name"].astype(str).str.casefold().eq(name.casefold()).any():
        raise ValueError(f"Credential profile '{name}' already exists.")
    credential_id = f"cred-{uuid.uuid4().hex[:10]}"
    row = {
        "credential_id": credential_id,
        "name": name,
        "version": str(values.get("version", "v2c")),
        "username": str(values.get("username", "")),
        "security_level": str(values.get("security_level", "community")),
        "secret_ref": str(values.get("secret_ref", "")),
        "notes": str(values.get("notes", "")),
        "is_active": "1",
        "created_at": utc_now(),
    }
    credentials = pd.concat([credentials, pd.DataFrame([row])], ignore_index=True)
    save_table("credentials", credentials)
    return credential_id


def save_device_template(values: Mapping[str, Any], oids: Iterable[Mapping[str, Any]]) -> str:
    ensure_monitoring_storage()
    templates = load_table("templates")
    name = str(values.get("name", "")).strip()
    if not name:
        raise ValueError("Template name is required.")
    if templates["name"].astype(str).str.casefold().eq(name.casefold()).any():
        raise ValueError(f"Template '{name}' already exists.")
    normalized_oids = []
    for item in oids:
        oid = str(item.get("oid", "")).strip().lstrip(".")
        if not oid or any(not part.isdigit() for part in oid.split(".")):
            raise ValueError(f"Invalid numeric OID: {oid or '(empty)'}")
        normalized_oids.append(
            {
                "name": str(item.get("name", "custom_metric")).strip(),
                "oid": oid,
                "unit": str(item.get("unit", "")).strip(),
            }
        )
    template_id = f"tpl-{uuid.uuid4().hex[:10]}"
    row = {
        "template_id": template_id,
        "name": name,
        "vendor": str(values.get("vendor", "Any")),
        "device_type": str(values.get("device_type", "Network Device")),
        "poll_interval_seconds": str(values.get("poll_interval_seconds", 300)),
        "oids_json": json.dumps(normalized_oids),
        "is_active": "1",
        "created_at": utc_now(),
    }
    templates = pd.concat([templates, pd.DataFrame([row])], ignore_index=True)
    save_table("templates", templates)
    return template_id


def _metric_value(device_id: str, metric: str, bucket: int) -> float:
    phase = _stable_int(device_id, metric, "phase", modulo=628) / 100
    jitter = (_stable_int(device_id, metric, bucket, modulo=1000) / 1000) - 0.5
    wave = math.sin((bucket / 5.0) + phase)
    if metric == "cpu":
        return max(3, min(97, 46 + 28 * wave + 22 * jitter))
    if metric == "memory":
        return max(12, min(96, 62 + 15 * wave + 18 * jitter))
    if metric == "temperature":
        return max(20, min(82, 43 + 12 * wave + 18 * jitter))
    return 0.0


def build_device_snapshot(device: Mapping[str, Any], at: datetime | None = None) -> dict[str, Any]:
    at = at or datetime.now(timezone.utc)
    bucket = int(at.timestamp() // 300)
    device_id = str(device.get("monitoring_device_id", "unknown"))
    down = _stable_int(device_id, bucket, "availability", modulo=37) == 0
    mode = str(device.get("poll_mode", "demo"))
    status = "DOWN" if down else "UP"
    if str(device.get("enabled", "1")) in {"0", "False", "false"}:
        status = "DISABLED"
    return {
        "monitoring_device_id": device_id,
        "inventory_device_id": str(device.get("inventory_device_id", "")),
        "device_name": str(device.get("display_name", "Unknown Device")),
        "host": str(device.get("host", "")),
        "vendor": str(device.get("vendor", "Unknown")),
        "model": str(device.get("model", "Unknown")),
        "datacenter_id": str(device.get("datacenter_id", "")),
        "status": status,
        "uptime_days": 0 if down else 30 + _stable_int(device_id, "uptime", modulo=700),
        "cpu_percent": round(_metric_value(device_id, "cpu", bucket), 1),
        "memory_percent": round(_metric_value(device_id, "memory", bucket), 1),
        "temperature_c": round(_metric_value(device_id, "temperature", bucket), 1),
        "network_ports": int(float(device.get("network_ports", 0) or 0)),
        "source": "DEMO" if mode == "demo" else "LAST KNOWN",
        "observed_at": at.isoformat(timespec="seconds"),
    }


def build_snapshot_table(devices_df: pd.DataFrame | None = None) -> pd.DataFrame:
    devices_df = list_monitoring_devices() if devices_df is None else devices_df
    if devices_df.empty:
        return pd.DataFrame()
    return pd.DataFrame(
        [build_device_snapshot(row.to_dict()) for _, row in devices_df.iterrows()]
    )


def build_interfaces(device: Mapping[str, Any]) -> pd.DataFrame:
    snapshot = build_device_snapshot(device)
    port_count = max(4, min(48, int(snapshot.get("network_ports", 0) or 4)))
    shown_ports = min(port_count, 24)
    device_id = snapshot["monitoring_device_id"]
    rows = []
    cisco_style = snapshot["vendor"].lower() == "cisco"
    for index in range(1, shown_ports + 1):
        down = _stable_int(device_id, index, "ifstatus", modulo=19) == 0
        rx = 0 if down else 15 + _stable_int(device_id, index, "rx", modulo=840)
        tx = 0 if down else 8 + _stable_int(device_id, index, "tx", modulo=460)
        errors = _stable_int(device_id, index, "errors", modulo=13)
        rows.append(
            {
                "interface": f"Gi1/0/{index}" if cisco_style else f"Eth0/{index}",
                "status": "DOWN" if down else "UP",
                "speed": "1 Gbps",
                "rx_mbps": rx,
                "tx_mbps": tx,
                "errors": errors,
                "utilization_percent": round(min(100, (rx + tx) / 10), 1),
                "source": snapshot["source"],
            }
        )
    return pd.DataFrame(rows)


def build_metric_history(
    device: Mapping[str, Any],
    metric: str,
    hours: int = 24,
    points: int = 96,
) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    timestamps = pd.date_range(end=now, periods=points, freq=f"{max(1, hours * 60 // points)}min")
    device_id = str(device.get("monitoring_device_id", "unknown"))
    metric_key = {
        "CPU": "cpu",
        "Memory": "memory",
        "Temperature": "temperature",
    }.get(metric, "cpu")
    values = []
    for timestamp in timestamps:
        bucket = int(timestamp.timestamp() // 300)
        values.append(round(_metric_value(device_id, metric_key, bucket), 1))
    unit = "°C" if metric_key == "temperature" else "%"
    return pd.DataFrame({"timestamp": timestamps, "value": values, "metric": metric, "unit": unit})


def sync_alerts(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    ensure_monitoring_storage()
    alerts = load_table("alerts")
    now = utc_now()
    active_keys: set[str] = set()

    def conditions(row: Mapping[str, Any]) -> list[tuple[str, str, float, float, str]]:
        found: list[tuple[str, str, float, float, str]] = []
        if str(row["status"]) == "DOWN":
            found.append(("availability", "CRITICAL", 0, 1, "Device is unreachable"))
        for metric, value, warning, critical, label in (
            ("cpu", float(row["cpu_percent"]), 80, 90, "CPU"),
            ("memory", float(row["memory_percent"]), 80, 90, "Memory"),
            ("temperature", float(row["temperature_c"]), 60, 70, "Temperature"),
        ):
            if value >= critical:
                found.append((metric, "CRITICAL", value, critical, f"{label} threshold exceeded"))
            elif value >= warning:
                found.append((metric, "WARNING", value, warning, f"{label} threshold exceeded"))
        return found

    for _, row in snapshot_df.iterrows():
        device_id = str(row["monitoring_device_id"])
        for metric, severity, value, threshold, message in conditions(row):
            key = f"{device_id}|{metric}"
            active_keys.add(key)
            match = alerts["dedupe_key"].astype(str) == key
            if match.any():
                idx = alerts[match].index[0]
                alerts.at[idx, "severity"] = severity
                alerts.at[idx, "status"] = (
                    alerts.at[idx, "status"]
                    if alerts.at[idx, "status"] == "ACKNOWLEDGED"
                    else "OPEN"
                )
                alerts.at[idx, "value"] = str(value)
                alerts.at[idx, "threshold"] = str(threshold)
                alerts.at[idx, "message"] = message
                alerts.at[idx, "last_seen_at"] = now
                alerts.at[idx, "resolved_at"] = ""
            else:
                new_row = {
                    "alert_id": f"alert-{uuid.uuid4().hex[:12]}",
                    "dedupe_key": key,
                    "monitoring_device_id": device_id,
                    "device_name": str(row["device_name"]),
                    "metric_name": metric,
                    "severity": severity,
                    "status": "OPEN",
                    "value": str(value),
                    "threshold": str(threshold),
                    "message": message,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "acknowledged_at": "",
                    "acknowledged_by": "",
                    "resolved_at": "",
                }
                alerts = pd.concat([alerts, pd.DataFrame([new_row])], ignore_index=True)

    open_mask = alerts["status"].astype(str).isin(["OPEN", "ACKNOWLEDGED"])
    stale_mask = open_mask & ~alerts["dedupe_key"].astype(str).isin(active_keys)
    if stale_mask.any():
        alerts.loc[stale_mask, "status"] = "RESOLVED"
        alerts.loc[stale_mask, "resolved_at"] = now
    save_table("alerts", alerts)
    return alerts


def acknowledge_alert(alert_id: str, username: str) -> None:
    alerts = load_table("alerts")
    match = alerts["alert_id"].astype(str) == str(alert_id)
    if not match.any():
        raise ValueError("Alert was not found.")
    idx = alerts[match].index[0]
    alerts.at[idx, "status"] = "ACKNOWLEDGED"
    alerts.at[idx, "acknowledged_at"] = utc_now()
    alerts.at[idx, "acknowledged_by"] = username
    save_table("alerts", alerts)


def record_trap(values: Mapping[str, Any]) -> str:
    ensure_monitoring_storage()
    traps = load_table("traps")
    trap_id = f"trap-{uuid.uuid4().hex[:12]}"
    row = {
        "trap_id": trap_id,
        "monitoring_device_id": str(values.get("monitoring_device_id", "")),
        "device_name": str(values.get("device_name", "Unknown Device")),
        "source_address": str(values.get("source_address", "")),
        "snmp_version": str(values.get("snmp_version", "v2c")),
        "enterprise_oid": str(values.get("enterprise_oid", "")),
        "trap_oid": str(values.get("trap_oid", "1.3.6.1.6.3.1.1.5.3")),
        "severity": str(values.get("severity", "WARNING")),
        "message": str(values.get("message", "SNMP trap received")),
        "varbinds_json": json.dumps(values.get("varbinds", {})),
        "status": "NEW",
        "received_at": utc_now(),
        "acknowledged_at": "",
        "acknowledged_by": "",
    }
    traps = pd.concat([traps, pd.DataFrame([row])], ignore_index=True)
    save_table("traps", traps)
    return trap_id


def acknowledge_trap(trap_id: str, username: str) -> None:
    traps = load_table("traps")
    match = traps["trap_id"].astype(str) == str(trap_id)
    if not match.any():
        raise ValueError("Trap was not found.")
    idx = traps[match].index[0]
    traps.at[idx, "status"] = "ACKNOWLEDGED"
    traps.at[idx, "acknowledged_at"] = utc_now()
    traps.at[idx, "acknowledged_by"] = username
    save_table("traps", traps)


def snmp_capabilities() -> dict[str, Any]:
    pysnmp_available = find_spec("pysnmp") is not None
    snmpget_path = shutil.which("snmpget")
    snmpwalk_path = shutil.which("snmpwalk")
    return {
        "pysnmp_available": pysnmp_available,
        "snmpget_available": bool(snmpget_path),
        "snmpwalk_available": bool(snmpwalk_path),
        "snmpget_path": snmpget_path or "",
        "snmpwalk_path": snmpwalk_path or "",
        "ready": bool(snmpget_path and snmpwalk_path),
        "message": (
            "Net-SNMP command line tools are ready."
            if snmpget_path and snmpwalk_path
            else "Install pysnmp or Net-SNMP tools to enable live SNMP GET/WALK."
        ),
    }


def _validate_oid(oid: str) -> str:
    normalized = str(oid).strip().lstrip(".")
    if not normalized or any(not part.isdigit() for part in normalized.split(".")):
        raise ValueError("OID must be a dotted numeric identifier.")
    return normalized


def snmp_query(
    *,
    operation: str,
    host: str,
    oid: str,
    version: str = "v2c",
    community: str = "",
    username: str = "",
    auth_protocol: str = "SHA",
    auth_key: str = "",
    priv_protocol: str = "AES",
    priv_key: str = "",
    timeout_seconds: int = 2,
    retries: int = 1,
) -> dict[str, Any]:
    """Run a bounded SNMP query through Net-SNMP when available."""
    operation = operation.upper()
    if operation not in {"GET", "WALK"}:
        raise ValueError("SNMP operation must be GET or WALK.")
    oid = _validate_oid(oid)
    binary = shutil.which("snmpget" if operation == "GET" else "snmpwalk")
    if not binary:
        return {
            "ok": False,
            "code": "dependency_missing",
            "message": snmp_capabilities()["message"],
            "output": "",
        }
    if not host.strip() or any(char.isspace() for char in host.strip()):
        raise ValueError("A valid host or IP address is required.")

    args = [
        binary,
        "-t",
        str(max(1, min(int(timeout_seconds), 15))),
        "-r",
        str(max(0, min(int(retries), 5))),
    ]
    if version in {"v1", "v2c"}:
        if not community:
            raise ValueError("Community is required for SNMP v1/v2c.")
        args.extend(["-v", "1" if version == "v1" else "2c", "-c", community])
    elif version == "v3":
        if not username:
            raise ValueError("Username is required for SNMP v3.")
        level = "noAuthNoPriv"
        args.extend(["-v", "3", "-u", username])
        if auth_key:
            level = "authNoPriv"
            args.extend(["-a", auth_protocol, "-A", auth_key])
        if priv_key:
            level = "authPriv"
            args.extend(["-x", priv_protocol, "-X", priv_key])
        args.extend(["-l", level])
    else:
        raise ValueError("Unsupported SNMP version.")
    args.extend([host.strip(), oid])

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=max(3, int(timeout_seconds) * (int(retries) + 2)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout", "message": "SNMP query timed out.", "output": ""}
    output = (completed.stdout or completed.stderr or "").strip()
    return {
        "ok": completed.returncode == 0,
        "code": "ok" if completed.returncode == 0 else "protocol_error",
        "message": "SNMP query completed." if completed.returncode == 0 else output,
        "output": output,
    }


def preview_discovery(cidr: str) -> pd.DataFrame:
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = list(itertools.islice(network.hosts(), 5))
    examples = [
        ("Cisco Router", "Cisco", "ISR Router"),
        ("Cisco Switch", "Cisco", "Catalyst C9300"),
        ("Windows Server", "Microsoft", "Windows Server"),
        ("HP Switch", "HPE", "Aruba Switch"),
        ("Network Printer", "Unknown", "Printer"),
    ]
    rows = []
    for index, (kind, vendor, model) in enumerate(examples):
        if index >= len(hosts):
            break
        rows.append(
            {
                "add": True,
                "ip_address": str(hosts[index]),
                "hostname": f"preview-{index + 1:02d}",
                "device_type": kind,
                "vendor": vendor,
                "model": model,
                "icmp": "PREVIEW",
                "snmp": "PREVIEW",
                "source": "PREVIEW — NO NETWORK TRAFFIC",
            }
        )
    return pd.DataFrame(rows)


def discover_icmp(cidr: str, max_hosts: int = 256, workers: int = 32) -> pd.DataFrame:
    network = ipaddress.ip_network(cidr, strict=False)
    usable_hosts = max(0, int(network.num_addresses) - (0 if network.prefixlen >= 31 else 2))
    if usable_hosts > max_hosts:
        raise ValueError(f"Discovery is limited to {max_hosts} hosts per scan.")
    hosts = list(network.hosts())

    windows = platform.system().lower().startswith("win")

    def ping_one(address: str) -> dict[str, Any]:
        command = (
            ["ping", "-n", "1", "-w", "900", address]
            if windows
            else ["ping", "-c", "1", "-W", "1", address]
        )
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            reachable = completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            reachable = False
        return {
            "add": reachable,
            "ip_address": address,
            "hostname": "",
            "device_type": "Unknown Device",
            "vendor": "Unknown",
            "model": "SNMP probe required",
            "icmp": "UP" if reachable else "NO RESPONSE",
            "snmp": "NOT TESTED",
            "source": "LIVE ICMP",
        }

    rows = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 64))) as executor:
        futures = {executor.submit(ping_one, str(host)): str(host) for host in hosts}
        for future in as_completed(futures):
            rows.append(future.result())
    return pd.DataFrame(rows).sort_values(
        "ip_address",
        key=lambda series: series.map(ipaddress.ip_address),
    ).reset_index(drop=True)


def add_discovery_results(rows: Iterable[Mapping[str, Any]], template_id: str, credential_id: str) -> int:
    added = 0
    for row in rows:
        if not bool(row.get("add", False)):
            continue
        host = str(row.get("ip_address", "")).strip()
        name = str(row.get("hostname", "")).strip() or str(row.get("device_type", "")).strip() or host
        try:
            add_monitoring_device(
                {
                    "display_name": name,
                    "host": host,
                    "vendor": row.get("vendor", "Unknown"),
                    "model": row.get("model", "Unknown"),
                    "template_id": template_id,
                    "credential_id": credential_id,
                    "poll_mode": "snmp",
                    "network_ports": 24,
                }
            )
            added += 1
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
    return added

