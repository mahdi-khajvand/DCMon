from __future__ import annotations

from pathlib import Path
from typing import Tuple

import pandas as pd

from .config import (
    DCS_FILE,
    DEVICES_FILE,
    LINKS_FILE,
    PLACEMENTS_FILE,
    RACKS_FILE,
)


def ensure_data_files() -> None:
    if not RACKS_FILE.exists():
        pd.DataFrame(
            columns=["rack_id", "rack_name", "datacenter_id", "total_units", "description"]
        ).to_csv(RACKS_FILE, index=False)
    if not DEVICES_FILE.exists():
        pd.DataFrame(
            columns=[
                "device_id",
                "device_name",
                "model",
                "units_used",
                "network_ports",
                "power_ports",
                "fiber_ports",
                "description",
                "datacenter_id",
            ]
        ).to_csv(DEVICES_FILE, index=False)
    if not PLACEMENTS_FILE.exists():
        pd.DataFrame(
            columns=["placement_id", "rack_id", "device_id", "start_unit", "end_unit"]
        ).to_csv(PLACEMENTS_FILE, index=False)
    if not DCS_FILE.exists():
        pd.DataFrame(
            [
                {
                    "datacenter_id": 1,
                    "datacenter_name": "Tehran DC-1",
                    "location": "Tehran",
                    "description": "Primary datacenter",
                }
            ]
        ).to_csv(DCS_FILE, index=False)
    if not LINKS_FILE.exists():
        pd.DataFrame(
            columns=[
                "link_id",
                "source_dc_id",
                "destination_dc_id",
                "link_type",
                "capacity_gbps",
                "status",
                "provider",
                "description",
            ]
        ).to_csv(LINKS_FILE, index=False)


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def load_inventory() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ensure_data_files()
    try:
        from . import db_backend
        if db_backend.is_db_mode():
            racks = db_backend.read_table("racks")
            devices = db_backend.read_table("devices")
            placements = db_backend.read_table("placements")
        else:
            racks = _read(RACKS_FILE)
            devices = _read(DEVICES_FILE)
            placements = _read(PLACEMENTS_FILE)
    except Exception:
        racks = _read(RACKS_FILE)
        devices = _read(DEVICES_FILE)
        placements = _read(PLACEMENTS_FILE)
    for df, cols in (
        (racks, ["rack_id", "total_units", "datacenter_id"]),
        (devices, ["device_id", "units_used", "network_ports", "power_ports", "fiber_ports", "datacenter_id"]),
        (placements, ["placement_id", "rack_id", "device_id", "start_unit", "end_unit"]),
    ):
        if df.empty:
            continue
        for c in cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
    if not racks.empty and "datacenter_id" not in racks.columns:
        racks["datacenter_id"] = 1
    return racks, devices, placements


def save_inventory(racks, devices, placements) -> None:
    try:
        from . import db_backend
        if db_backend.is_db_mode():
            db_backend.write_table("racks", racks)
            db_backend.write_table("devices", devices)
            db_backend.write_table("placements", placements)
            return
    except Exception:
        pass
    racks.to_csv(RACKS_FILE, index=False)
    devices.to_csv(DEVICES_FILE, index=False)
    placements.to_csv(PLACEMENTS_FILE, index=False)


def load_datacenters() -> Tuple[pd.DataFrame, pd.DataFrame]:
    ensure_data_files()
    try:
        from . import db_backend
        if db_backend.is_db_mode():
            return db_backend.read_table("datacenters"), db_backend.read_table("dci_links")
    except Exception:
        pass
    return _read(DCS_FILE), _read(LINKS_FILE)


def save_datacenters(dcs: pd.DataFrame, links: pd.DataFrame) -> None:
    try:
        from . import db_backend
        if db_backend.is_db_mode():
            db_backend.write_table("datacenters", dcs)
            db_backend.write_table("dci_links", links)
            return
    except Exception:
        pass
    dcs.to_csv(DCS_FILE, index=False)
    links.to_csv(LINKS_FILE, index=False)


def generate_rack_id(racks_df: pd.DataFrame, datacenter_id: int) -> int:
    try:
        dc_racks = racks_df[
            pd.to_numeric(racks_df.get("datacenter_id", 1), errors="coerce") == int(datacenter_id)
        ]
        if dc_racks.empty:
            seq = 1
        else:
            existing = pd.to_numeric(dc_racks["rack_id"], errors="coerce").dropna().astype(int)
            seq = int((existing % 1000).max() + 1) if not existing.empty else 1
        return int(datacenter_id) * 1000 + seq
    except Exception:
        return int(datacenter_id) * 1000 + (len(racks_df) + 1)


def next_device_id(devices_df: pd.DataFrame) -> int:
    if devices_df.empty:
        return 5001
    return int(pd.to_numeric(devices_df["device_id"], errors="coerce").max()) + 1


def next_placement_id(placements_df: pd.DataFrame) -> int:
    if placements_df.empty:
        return 1
    return int(pd.to_numeric(placements_df["placement_id"], errors="coerce").max()) + 1


def create_rack(racks_df, rack_name, datacenter_id, total_units, description):
    if not str(rack_name).strip():
        return racks_df, False, "نام رک خالی است."
    rack_id = generate_rack_id(racks_df, datacenter_id)
    row = {
        "rack_id": int(rack_id),
        "rack_name": str(rack_name).strip(),
        "datacenter_id": int(datacenter_id),
        "total_units": int(total_units),
        "description": str(description or "").strip(),
    }
    racks_df = pd.concat([racks_df, pd.DataFrame([row])], ignore_index=True)
    return racks_df, True, f"رک ثبت شد · ID={rack_id}"


def create_device(
    devices_df,
    device_name,
    model,
    units_used,
    network_ports,
    power_ports,
    fiber_ports,
    description,
    datacenter_id,
):
    if not str(device_name).strip():
        return devices_df, False, "نام دستگاه خالی است."
    device_id = next_device_id(devices_df)
    row = {
        "device_id": int(device_id),
        "device_name": str(device_name).strip(),
        "model": str(model or "").strip(),
        "units_used": int(units_used),
        "network_ports": int(network_ports),
        "power_ports": int(power_ports),
        "fiber_ports": int(fiber_ports),
        "description": str(description or "").strip(),
        "datacenter_id": int(datacenter_id) if datacenter_id is not None else 1,
    }
    devices_df = pd.concat([devices_df, pd.DataFrame([row])], ignore_index=True)
    return devices_df, True, f"دستگاه ثبت شد · ID={device_id}"


def validate_overlap(placements_df, rack_id, start_unit, end_unit):
    rack_p = placements_df[placements_df["rack_id"].astype(int) == int(rack_id)]
    for _, ex in rack_p.iterrows():
        es, ee = int(ex["start_unit"]), int(ex["end_unit"])
        if not (end_unit < es or start_unit > ee):
            return False, f"تداخل یونیت با بازه [{es}-{ee}]"
    return True, ""


def place_device(racks_df, devices_df, placements_df, rack_id, device_id, start_unit):
    rack = racks_df[racks_df["rack_id"].astype(int) == int(rack_id)]
    dev = devices_df[devices_df["device_id"].astype(int) == int(device_id)]
    if rack.empty:
        return placements_df, False, "رک یافت نشد."
    if dev.empty:
        return placements_df, False, "دستگاه یافت نشد."
    units = int(dev.iloc[0]["units_used"])
    total = int(rack.iloc[0]["total_units"])
    start = int(start_unit)
    end = start + units - 1
    if start < 1 or end > total:
        return placements_df, False, f"خارج از ظرفیت رک (1-{total})"
    ok, msg = validate_overlap(placements_df, rack_id, start, end)
    if not ok:
        return placements_df, False, msg
    pid = next_placement_id(placements_df)
    row = {
        "placement_id": pid,
        "rack_id": int(rack_id),
        "device_id": int(device_id),
        "start_unit": start,
        "end_unit": end,
    }
    placements_df = pd.concat([placements_df, pd.DataFrame([row])], ignore_index=True)
    return placements_df, True, f"استقرار ثبت شد · placement_id={pid}"


def apply_dc_scope(racks, devices, placements, allowed):
    if allowed is None or allowed == "all":
        return racks, devices, placements
    allowed = set(int(x) for x in allowed)
    if racks.empty or "datacenter_id" not in racks.columns:
        return racks, devices, placements
    sr = racks[racks["datacenter_id"].astype(int).isin(allowed)].copy()
    sd = devices
    if not devices.empty and "datacenter_id" in devices.columns:
        sd = devices[devices["datacenter_id"].astype(int).isin(allowed)].copy()
    rack_ids = set(sr["rack_id"].astype(int)) if not sr.empty else set()
    device_ids = set(sd["device_id"].astype(int)) if not sd.empty else set()
    sp = placements
    if not placements.empty:
        sp = placements[
            placements["rack_id"].astype(int).isin(rack_ids)
            & placements["device_id"].astype(int).isin(device_ids)
        ].copy()
    return sr, sd, sp


def inventory_summary(racks, devices, placements) -> dict:
    total_units = int(pd.to_numeric(racks.get("total_units", 0), errors="coerce").fillna(0).sum()) if not racks.empty else 0
    used = 0
    if not placements.empty and not devices.empty:
        merged = placements.merge(
            devices[["device_id", "units_used"]], on="device_id", how="left"
        )
        used = int(pd.to_numeric(merged["units_used"], errors="coerce").fillna(0).sum())
    return {
        "racks": len(racks),
        "devices": len(devices),
        "placements": len(placements),
        "total_units": total_units,
        "used_units": used,
        "free_units": max(0, total_units - used),
    }


def delete_device(racks, devices, placements, device_id: int):
    """Remove device + its placements. Connections cleaned separately by caller."""
    did = int(device_id)
    if devices.empty or did not in pd.to_numeric(devices["device_id"], errors="coerce").dropna().astype(int).values:
        return racks, devices, placements, False, "دستگاه یافت نشد"
    devices = devices[pd.to_numeric(devices["device_id"], errors="coerce") != did].copy()
    if not placements.empty:
        placements = placements[pd.to_numeric(placements["device_id"], errors="coerce") != did].copy()
    return racks, devices, placements, True, f"دستگاه {did} حذف شد"


def delete_rack(racks, devices, placements, rack_id: int):
    """Remove rack + placements on it."""
    rid = int(rack_id)
    if racks.empty or rid not in pd.to_numeric(racks["rack_id"], errors="coerce").dropna().astype(int).values:
        return racks, devices, placements, False, "رک یافت نشد"
    racks = racks[pd.to_numeric(racks["rack_id"], errors="coerce") != rid].copy()
    if not placements.empty:
        placements = placements[pd.to_numeric(placements["rack_id"], errors="coerce") != rid].copy()
    return racks, devices, placements, True, f"رک {rid} حذف شد"


def delete_datacenter(dcs, links, racks, devices, placements, datacenter_id: int):
    """Remove DC and cascade racks (and their placements) under it. DCI links involving DC removed."""
    dcid = int(datacenter_id)
    if dcs.empty or dcid not in pd.to_numeric(dcs["datacenter_id"], errors="coerce").dropna().astype(int).values:
        return dcs, links, racks, devices, placements, False, "دیتاسنتر یافت نشد"
    dcs = dcs[pd.to_numeric(dcs["datacenter_id"], errors="coerce") != dcid].copy()
    if not links.empty:
        src = pd.to_numeric(links.get("source_dc_id"), errors="coerce")
        dst = pd.to_numeric(links.get("destination_dc_id"), errors="coerce")
        links = links[~((src == dcid) | (dst == dcid))].copy()
    rack_ids = set()
    if not racks.empty and "datacenter_id" in racks.columns:
        mask = pd.to_numeric(racks["datacenter_id"], errors="coerce") == dcid
        rack_ids = set(pd.to_numeric(racks.loc[mask, "rack_id"], errors="coerce").dropna().astype(int))
        racks = racks[~mask].copy()
    if rack_ids and not placements.empty:
        placements = placements[~pd.to_numeric(placements["rack_id"], errors="coerce").isin(rack_ids)].copy()
    if not devices.empty and "datacenter_id" in devices.columns:
        # keep devices but optional: leave devices orphaned for reassignment; user may still want them
        pass
    return dcs, links, racks, devices, placements, True, f"دیتاسنتر {dcid} حذف شد"
