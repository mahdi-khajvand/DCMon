"""
PostgreSQL storage backend for DC Monitor.
When enabled, inventory/connection tables live in DB instead of CSV.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .config import DATA_DIR

SETTINGS_FILE = DATA_DIR / "storage_settings.json"

# Table name -> ordered columns matching CSV schema
TABLE_SCHEMAS: Dict[str, List[str]] = {
    "datacenters": [
        "datacenter_id",
        "datacenter_name",
        "location",
        "description",
    ],
    "dci_links": [
        "link_id",
        "source_dc_id",
        "destination_dc_id",
        "link_type",
        "capacity_gbps",
        "status",
        "provider",
        "description",
    ],
    "racks": [
        "rack_id",
        "rack_name",
        "total_units",
        "description",
        "datacenter_id",
    ],
    "devices": [
        "device_id",
        "device_name",
        "model",
        "units_used",
        "network_ports",
        "power_ports",
        "fiber_ports",
        "description",
        "datacenter_id",
    ],
    "placements": [
        "placement_id",
        "rack_id",
        "device_id",
        "start_unit",
        "end_unit",
    ],
    "connections": [
        "connection_id",
        "source_rack_id",
        "source_device_id",
        "source_port",
        "destination_rack_id",
        "destination_device_id",
        "destination_port",
        "connection_type",
        "status",
        "notes",
    ],
}

DDL = {
    "datacenters": """
        CREATE TABLE IF NOT EXISTS datacenters (
            datacenter_id INTEGER PRIMARY KEY,
            datacenter_name TEXT NOT NULL,
            location TEXT,
            description TEXT
        )
    """,
    "dci_links": """
        CREATE TABLE IF NOT EXISTS dci_links (
            link_id INTEGER PRIMARY KEY,
            source_dc_id INTEGER,
            destination_dc_id INTEGER,
            link_type TEXT,
            capacity_gbps DOUBLE PRECISION,
            status TEXT,
            provider TEXT,
            description TEXT
        )
    """,
    "racks": """
        CREATE TABLE IF NOT EXISTS racks (
            rack_id INTEGER PRIMARY KEY,
            rack_name TEXT NOT NULL,
            total_units INTEGER,
            description TEXT,
            datacenter_id INTEGER
        )
    """,
    "devices": """
        CREATE TABLE IF NOT EXISTS devices (
            device_id INTEGER PRIMARY KEY,
            device_name TEXT NOT NULL,
            model TEXT,
            units_used INTEGER,
            network_ports INTEGER,
            power_ports INTEGER,
            fiber_ports INTEGER,
            description TEXT,
            datacenter_id INTEGER
        )
    """,
    "placements": """
        CREATE TABLE IF NOT EXISTS placements (
            placement_id INTEGER PRIMARY KEY,
            rack_id INTEGER,
            device_id INTEGER,
            start_unit INTEGER,
            end_unit INTEGER
        )
    """,
    "connections": """
        CREATE TABLE IF NOT EXISTS connections (
            connection_id INTEGER PRIMARY KEY,
            source_rack_id INTEGER,
            source_device_id INTEGER,
            source_port TEXT,
            destination_rack_id INTEGER,
            destination_device_id INTEGER,
            destination_port TEXT,
            connection_type TEXT,
            status TEXT,
            notes TEXT
        )
    """,
}

DEFAULT_SETTINGS = {
    "mode": "csv",  # csv | postgres
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "",
    "database": "datacenter-monitoring",
    "connected": False,
    "last_error": "",
    "last_ok": "",
}


def load_settings() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_FILE.exists():
        save_settings(DEFAULT_SETTINGS.copy())
        return DEFAULT_SETTINGS.copy()
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        out = DEFAULT_SETTINGS.copy()
        out.update({k: data.get(k, out[k]) for k in out})
        return out
    except Exception:
        return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_db_mode() -> bool:
    s = load_settings()
    return s.get("mode") == "postgres" and bool(s.get("connected"))


def _connect_kwargs(settings: dict, database: Optional[str] = None) -> dict:
    return {
        "host": settings.get("host") or "localhost",
        "port": int(settings.get("port") or 5432),
        "user": settings.get("user") or "postgres",
        "password": settings.get("password") or "",
        "dbname": database if database is not None else (settings.get("database") or "datacenter-monitoring"),
        "connect_timeout": 8,
    }


def _import_psycopg():
    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        return psycopg2, ISOLATION_LEVEL_AUTOCOMMIT
    except ImportError as e:
        raise RuntimeError(
            "پکیج psycopg2 نصب نیست. در سرور اجرا کنید: pip install psycopg2-binary"
        ) from e


def test_and_setup(settings: dict, migrate_from_csv: bool = True) -> dict:
    """
    Connect to Postgres server, create database if needed, create tables,
    optionally migrate CSV data when tables are empty.
    """
    psycopg2, ISOLATION_LEVEL_AUTOCOMMIT = _import_psycopg()
    db_name = (settings.get("database") or "datacenter-monitoring").strip()
    if not db_name:
        db_name = "datacenter-monitoring"

    # 1) connect to maintenance DB to create target database
    admin_kw = _connect_kwargs(settings, database="postgres")
    try:
        conn = psycopg2.connect(**admin_kw)
    except Exception:
        # some installs use template1 only
        admin_kw = _connect_kwargs(settings, database="template1")
        conn = psycopg2.connect(**admin_kw)

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
    exists = cur.fetchone() is not None
    if not exists:
        # quoted identifier for hyphens
        cur.execute(f'CREATE DATABASE "{db_name}"')
    cur.close()
    conn.close()

    # 2) connect to target DB and create schema
    app_kw = _connect_kwargs(settings, database=db_name)
    conn = psycopg2.connect(**app_kw)
    cur = conn.cursor()
    for ddl in DDL.values():
        cur.execute(ddl)
    conn.commit()

    migrated = []
    if migrate_from_csv:
        for table, cols in TABLE_SCHEMAS.items():
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            if count == 0:
                n = _migrate_csv_table(conn, table, cols)
                if n:
                    migrated.append(f"{table}:{n}")

    cur.close()
    conn.close()

    settings = {**settings, "mode": "postgres", "connected": True, "database": db_name,
                "last_error": "", "last_ok": f"connected · db={db_name}" + (f" · migrated {', '.join(migrated)}" if migrated else "")}
    save_settings(settings)
    return settings


def _migrate_csv_table(conn, table: str, cols: List[str]) -> int:
    from .config import (
        DCS_FILE,
        DEVICES_FILE,
        LINKS_FILE,
        PLACEMENTS_FILE,
        RACKS_FILE,
        DATA_DIR,
    )

    path_map = {
        "datacenters": DCS_FILE,
        "dci_links": LINKS_FILE,
        "racks": RACKS_FILE,
        "devices": DEVICES_FILE,
        "placements": PLACEMENTS_FILE,
        "connections": DATA_DIR / "connections.csv",
    }
    path = path_map.get(table)
    if not path or not path.exists():
        return 0
    try:
        df = pd.read_csv(path)
    except Exception:
        return 0
    if df.empty:
        return 0
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    return write_table_df(conn, table, cols, df)


def _to_python(val: Any) -> Any:
    """Convert pandas/numpy scalars to native Python types for psycopg2."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    # numpy / pandas integer / float / bool
    if hasattr(val, "item"):
        try:
            return val.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(val, (pd.Timestamp,)):
        return val.to_pydatetime() if hasattr(val, "to_pydatetime") else str(val)
    return val


def write_table_df(conn, table: str, cols: List[str], df: pd.DataFrame) -> int:
    from psycopg2.extras import execute_values

    if df is None or df.empty:
        return 0
    rows = []
    for _, r in df.iterrows():
        rows.append(tuple(_to_python(r.get(c)) for c in cols))
    if not rows:
        return 0
    cur = conn.cursor()
    placeholders = "(" + ",".join(["%s"] * len(cols)) + ")"
    col_list = ", ".join(cols)
    # clear then insert for full rewrite
    cur.execute(f"DELETE FROM {table}")
    execute_values(cur, f"INSERT INTO {table} ({col_list}) VALUES %s", rows, template=placeholders)
    conn.commit()
    cur.close()
    return len(rows)


def get_connection():
    if not is_db_mode():
        return None
    psycopg2, _ = _import_psycopg()
    s = load_settings()
    return psycopg2.connect(**_connect_kwargs(s))


def read_table(table: str) -> pd.DataFrame:
    cols = TABLE_SCHEMAS[table]
    if not is_db_mode():
        return pd.DataFrame(columns=cols)
    conn = get_connection()
    try:
        df = pd.read_sql_query(f"SELECT {', '.join(cols)} FROM {table}", conn)
        return df
    finally:
        conn.close()


def write_table(table: str, df: pd.DataFrame) -> None:
    cols = TABLE_SCHEMAS[table]
    if not is_db_mode():
        return
    if df is None:
        df = pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols].copy()
    conn = get_connection()
    try:
        write_table_df(conn, table, cols, df)
    finally:
        conn.close()


def switch_to_csv() -> dict:
    s = load_settings()
    s["mode"] = "csv"
    s["connected"] = False
    s["last_ok"] = "CSV mode"
    s["last_error"] = ""
    save_settings(s)
    return s
